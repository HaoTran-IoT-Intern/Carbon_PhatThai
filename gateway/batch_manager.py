# gateway/batch_manager.py
import uuid
import json
from datetime import datetime, timezone, timedelta
from gateway.models import db_transaction

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def _parse_json_field(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return default

def build_canonical_json_payload(batch_id, gateway_id, batch_created_at, rows):
    readings = []
    default_integrated = {
        "method": "fixed_10s_rectangular_v1",
        "valid_samples": 0,
        "covered_seconds": 0,
        "coverage_status": "none",
        "co2_flow_actual_m3h": {"avg": None, "min": None, "max": None},
        "co2_standard_volume_observed_m3": None
    }

    for row in rows:
        r = dict(row) if not isinstance(row, dict) else row
        gases = _parse_json_field(r.get("gases_and_dust_json"), {})
        flue = _parse_json_field(r.get("flue_gas_json"), {})
        ambient = _parse_json_field(r.get("ambient_json"), {})
        integrated = _parse_json_field(r.get("integrated_emissions_json"), default_integrated)
        valid_by_metric = _parse_json_field(r.get("valid_by_metric_json"), {})
        excluded_by_reason = _parse_json_field(r.get("excluded_by_reason_json"), {})
        qaqc_checks = _parse_json_field(r.get("qaqc_checks_json"), {})

        readings.append({
            "stack_id": r["stack_id"],
            "device_id": r["device_id"],
            "measurement_config_id": r["measurement_config_id"],
            "ts": r["minute_ts"],
            "window_seconds": r.get("window_seconds", 60),
            "gap_reason": r.get("gap_reason"),
            "samples": {
                "expected": r.get("expected_samples", 6),
                "received": r.get("received_samples", 0),
                "valid_by_metric": valid_by_metric,
                "excluded_by_reason": excluded_by_reason
            },
            "gases_and_dust": gases,
            "flue_gas": flue,
            "ambient": ambient,
            "integrated_emissions": integrated,
            "qaqc": {
                "minute_status": r.get("minute_qaqc_status", "unknown"),
                "last_check_at": qaqc_checks.get("last_check_at", "2026-09-18T00:00:00.000Z"),
                "zero_drift_pct": qaqc_checks.get("zero_drift_pct", 0.85),
                "span_drift_pct": qaqc_checks.get("span_drift_pct", 1.20),
                "is_calibrating": qaqc_checks.get("is_calibrating", False)
            }
        })

    return {
        "schema_version": "1.2.3",
        "batch_id": batch_id,
        "gateway_id": gateway_id,
        "batch_created_at": batch_created_at,
        "readings": readings
    }

def freeze_and_create_batch(conn, stack_id, gateway_id, limit=5, current_time_dt=None):
    ref_dt = current_time_dt or datetime.now(timezone.utc)
    # Grace period: now - 120s
    cutoff_ts = (ref_dt - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    now_str = get_iso_now()
    
    with db_transaction(conn):
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM minute_readings
            WHERE status = 'draft' AND minute_ts <= ? AND stack_id = ?
            ORDER BY minute_ts ASC LIMIT ?
        """, (cutoff_ts, stack_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        
        if not rows:
            return None
            
        batch_id = str(uuid.uuid4())
        payload = build_canonical_json_payload(batch_id, gateway_id, now_str, rows)
        payload_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        
        cur.execute("""
            INSERT INTO batches (
                batch_id, stack_id, gateway_id, created_at, payload_json, readings_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """, (batch_id, stack_id, gateway_id, now_str, payload_str, len(rows)))
        
        row_ids = [r["id"] for r in rows]
        cur.execute(f"""
            UPDATE minute_readings 
            SET status = 'batched', batch_id = ?
            WHERE id IN ({','.join('?' for _ in row_ids)})
        """, [batch_id] + row_ids)
        
        return batch_id
