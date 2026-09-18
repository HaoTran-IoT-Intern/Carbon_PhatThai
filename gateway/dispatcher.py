# gateway/dispatcher.py
import json
import logging
import time
import random
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from gateway.models import db_transaction

logger = logging.getLogger(__name__)

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def parse_retry_after(header_val, default_sec=30):
    if not header_val:
        return default_sec
    try:
        return int(header_val)
    except ValueError:
        try:
            target_dt = parsedate_to_datetime(header_val)
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
            delta = (target_dt - datetime.now(timezone.utc)).total_seconds()
            return max(5, int(delta))
        except Exception:
            return default_sec

def validate_ack(batch, resp_body, readings_count):
    if not isinstance(resp_body, dict):
        return False
    return (
        resp_body.get("status") == "success" and
        resp_body.get("batch_id") == batch["batch_id"] and
        resp_body.get("processed_count") == readings_count and
        (resp_body.get("inserted_count", 0) + resp_body.get("duplicated_count", 0)) == readings_count
    )

def resume_suspended_stack(conn, stack_id):
    with db_transaction(conn):
        conn.execute("DELETE FROM gateway_system_state WHERE key = ?", (f"stack_suspended:{stack_id}",))

def resume_dispatcher(conn):
    with db_transaction(conn):
        conn.execute("DELETE FROM gateway_system_state WHERE key IN ('dispatcher_halted', 'dispatcher_cooldown_until', 'consecutive_transient_failures')")

def handle_transient_error(conn, batch, code, err_msg, now_str):
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'consecutive_transient_failures'")
    r = cur.fetchone()
    failures = int(r[0]) + 1 if r else 1
    
    backoff_sec = min(300, int(5 * (2 ** min(failures, 6))))
    jitter = random.uniform(-0.1, 0.1) * backoff_sec
    cooldown_sec = max(5, int(backoff_sec + jitter))
    
    cool_dt = (datetime.now(timezone.utc) + timedelta(seconds=cooldown_sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('consecutive_transient_failures', ?, ?)", (str(failures), now_str))
    cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_cooldown_until', ?, ?)", (cool_dt, now_str))
    cur.execute("UPDATE batches SET last_http_code = ?, last_error_message = ?, next_retry_at = ? WHERE batch_id = ?", 
                (code, err_msg, cool_dt, batch["batch_id"]))
    return {"status": "transient_error", "cooldown_sec": cooldown_sec}

def dispatch_single_batch(conn, http_client, config, pacing_seconds=1.0):
    if pacing_seconds > 0:
        time.sleep(pacing_seconds)
        
    now_str = get_iso_now()
    cur = conn.cursor()
    
    # 1. Check Halt (401)
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_halted'")
    r_halt = cur.fetchone()
    if r_halt and r_halt[0] == "1":
        return {"status": "halted"}
        
    # 2. Check station-wide cooldown (429 or transient backoff)
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    r_cool = cur.fetchone()
    if r_cool and r_cool[0] > now_str:
        return {"status": "cooldown", "until": r_cool[0]}
        
    # 3. Suspended stacks list (403)
    cur.execute("SELECT key FROM gateway_system_state WHERE key LIKE 'stack_suspended:%'")
    suspended_stacks = [r[0].split("stack_suspended:", 1)[1] for r in cur.fetchall() if r[0].startswith("stack_suspended:")]
    
    # 4. Fetch oldest pending batch for non-suspended stacks
    query = """
        SELECT * FROM batches 
        WHERE status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= ?)
    """
    params = [now_str]
    if suspended_stacks:
        query += f" AND stack_id NOT IN ({','.join('?' for _ in suspended_stacks)})"
        params.extend(suspended_stacks)
    query += " ORDER BY created_at ASC LIMIT 1"
    
    cur.execute(query, params)
    batch = cur.fetchone()
    if not batch:
        return {"status": "idle"}
        
    batch = dict(batch)
    try:
        readings = json.loads(batch["payload_json"]).get("readings", [])
    except Exception as e:
        logger.critical(f"Corrupt payload_json for batch {batch['batch_id']}: {e}")
        with db_transaction(conn):
            cur.execute(
                "UPDATE batches SET status = 'quarantined', last_error_message = ? WHERE batch_id = ?",
                (f"CORRUPT_PAYLOAD_JSON: {str(e)}"[:255], batch["batch_id"]),
            )
            cur.execute(
                "UPDATE minute_readings SET status = 'quarantined' WHERE batch_id = ?",
                (batch["batch_id"],),
            )
        return {"status": "quarantined"}
    
    # 5. Send HTTP request
    try:
        resp = http_client.post(
            config["api_endpoint"],
            headers={"Content-Type": "application/json", "X-API-Key": config["api_key"]},
            data=batch["payload_json"],
            timeout=15
        )
        status_code = resp.status_code
        try:
            resp_body = resp.json()
        except Exception:
            resp_body = {"text": getattr(resp, "text", "")}
    except Exception as e:
        status_code = 0
        resp_body = {"error": str(e)}
        
    with db_transaction(conn):
        cur.execute("UPDATE batches SET attempt_count = attempt_count + 1, last_attempt_at = ? WHERE batch_id = ?",
                    (now_str, batch["batch_id"]))
        
        # A. 200 OK
        if status_code == 200 and validate_ack(batch, resp_body, len(readings)):
            cur.execute("UPDATE batches SET status = 'synced', synced_at = ? WHERE batch_id = ?", (now_str, batch["batch_id"]))
            cur.execute("UPDATE minute_readings SET status = 'synced', synced_at = ? WHERE batch_id = ?", (now_str, batch["batch_id"]))
            cur.execute("DELETE FROM gateway_system_state WHERE key IN ('consecutive_transient_failures', 'dispatcher_cooldown_until')")
            return {"status": "synced"}
            
        elif status_code == 200:
            return handle_transient_error(conn, batch, 200, "INVALID_ACK", now_str)
            
        # B. 401 Unauthorized
        elif status_code == 401:
            cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_halted', '1', ?)", (now_str,))
            cur.execute("UPDATE batches SET last_http_code = 401, last_error_message = ? WHERE batch_id = ?", (json.dumps(resp_body)[:255], batch["batch_id"]))
            return {"status": "halted"}
            
        # C. 403 Forbidden
        elif status_code == 403:
            cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES (?, '403_FORBIDDEN', ?)", 
                        (f"stack_suspended:{batch['stack_id']}", now_str))
            cur.execute("UPDATE batches SET last_http_code = 403, last_error_message = ? WHERE batch_id = ?", (json.dumps(resp_body)[:255], batch["batch_id"]))
            return {"status": "stack_suspended"}
            
        # D. 400 / 409 Conflict
        elif status_code in (400, 409):
            cur.execute("UPDATE batches SET status = 'quarantined', last_http_code = ?, last_error_message = ? WHERE batch_id = ?",
                        (status_code, json.dumps(resp_body)[:255], batch["batch_id"]))
            cur.execute("UPDATE minute_readings SET status = 'quarantined' WHERE batch_id = ?", (batch["batch_id"],))
            return {"status": "quarantined"}
            
        # E. 429 Too Many Requests
        elif status_code == 429:
            retry_header = None
            if hasattr(resp, "headers") and resp.headers and hasattr(resp.headers, "get"):
                retry_header = resp.headers.get("Retry-After")
            retry_sec = parse_retry_after(retry_header, 30)
            cool_dt = (datetime.now(timezone.utc) + timedelta(seconds=retry_sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_cooldown_until', ?, ?)", (cool_dt, now_str))
            cur.execute("UPDATE batches SET last_http_code = 429, last_error_message = 'Rate Limited' WHERE batch_id = ?", (batch["batch_id"],))
            return {"status": "rate_limited"}
            
        # F. 5xx / Network Error
        else:
            return handle_transient_error(conn, batch, status_code, json.dumps(resp_body)[:255], now_str)
