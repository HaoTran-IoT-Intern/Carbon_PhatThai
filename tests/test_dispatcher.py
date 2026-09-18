# tests/test_dispatcher.py
import pytest
from unittest.mock import MagicMock, patch
from gateway.models import init_db, get_db_connection
from gateway.batch_manager import freeze_and_create_batch
from gateway.aggregator import startup_gap_backfill
from gateway.dispatcher import (
    dispatch_single_batch,
    resume_suspended_stack,
    resume_dispatcher,
    parse_retry_after,
    validate_ack,
)
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

@pytest.fixture
def setup_gateway(tmp_path):
    db_path = str(tmp_path / "test_cems.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    now = datetime.now(timezone.utc)
    config = {
        "config_id": "cfg_test",
        "gateway_id": "gw_01",
        "api_endpoint": "http://mock-backend/api",
        "api_key": "test_key",
        "operational_stacks": [
            {
                "stack_id": "stack_01",
                "device_id": "node_01",
                "is_active": True,
                "active_from": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:00.000Z")
            }
        ]
    }
    startup_gap_backfill(conn, config)
    freeze_and_create_batch(conn, "stack_01", "gw_01", limit=2)
    return conn, config

def test_dispatcher_200_ok_marks_synced(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM batches LIMIT 1")
    b_id = cur.fetchone()[0]
    
    # Pre-populate transient failure keys in gateway_system_state to verify they get cleared
    cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('consecutive_transient_failures', '2', '2026-09-18T00:00:00.000Z')")
    cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_cooldown_until', '2026-09-18T00:00:00.000Z', '2026-09-18T00:00:00.000Z')")
    
    mock_resp.json.return_value = {
        "status": "success",
        "batch_id": b_id,
        "processed_count": 2,
        "inserted_count": 2,
        "duplicated_count": 0
    }
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "synced"
    
    # Verify batch status and synced_at
    cur.execute("SELECT status, synced_at, attempt_count, last_attempt_at FROM batches WHERE batch_id = ?", (b_id,))
    batch_row = cur.fetchone()
    assert batch_row["status"] == "synced"
    assert batch_row["synced_at"] is not None
    assert batch_row["attempt_count"] == 1
    assert batch_row["last_attempt_at"] is not None
    
    # Verify minute_readings status
    cur.execute("SELECT status, synced_at FROM minute_readings WHERE batch_id = ?", (b_id,))
    readings = cur.fetchall()
    assert len(readings) == 2
    for r in readings:
        assert r["status"] == "synced"
        assert r["synced_at"] is not None
        
    # Verify transient failure state keys cleared
    cur.execute("SELECT key FROM gateway_system_state WHERE key IN ('consecutive_transient_failures', 'dispatcher_cooldown_until')")
    assert cur.fetchall() == []

def test_dispatcher_200_invalid_ack(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM batches LIMIT 1")
    b_id = cur.fetchone()[0]
    
    # Invalid processed_count mismatch
    mock_resp.json.return_value = {
        "status": "success",
        "batch_id": b_id,
        "processed_count": 99,
        "inserted_count": 99,
        "duplicated_count": 0
    }
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "transient_error"
    assert res["cooldown_sec"] >= 5
    
    # Verify batch remains pending with error message
    cur.execute("SELECT status, last_http_code, last_error_message, next_retry_at FROM batches WHERE batch_id = ?", (b_id,))
    batch_row = cur.fetchone()
    assert batch_row["status"] == "pending"
    assert batch_row["last_http_code"] == 200
    assert batch_row["last_error_message"] == "INVALID_ACK"
    assert batch_row["next_retry_at"] is not None

def test_dispatcher_401_halt_and_resume(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"status": "error", "message": "Invalid API key"}
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "halted"
    
    # Verify gateway_system_state has dispatcher_halted
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_halted'")
    r = cur.fetchone()
    assert r is not None
    assert r[0] == "1"
    
    # Next dispatch call halts immediately without HTTP request
    mock_http.reset_mock()
    res_halted = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res_halted["status"] == "halted"
    mock_http.post.assert_not_called()
    
    # Resume dispatcher
    resume_dispatcher(conn)
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_halted'")
    assert cur.fetchone() is None

def test_dispatcher_403_suspends_stack_and_resume(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json.return_value = {"status": "error", "message": "Stack not authorized"}
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "stack_suspended"
    
    # Check DB records stack_suspended:stack_01
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'stack_suspended:stack_01'")
    assert cur.fetchone() is not None
    
    # Next dispatch is idle because stack_01 is suspended
    res_idle = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res_idle["status"] == "idle"
    
    # Resume stack
    resume_suspended_stack(conn, "stack_01")
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'stack_suspended:stack_01'")
    assert cur.fetchone() is None

def test_dispatcher_400_quarantines_batch_and_readings(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"status": "error", "message": "Schema validation failed"}
    mock_http.post.return_value = mock_resp
    
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM batches LIMIT 1")
    b_id = cur.fetchone()[0]
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "quarantined"
    
    # Verify batch is quarantined
    cur.execute("SELECT status, last_http_code FROM batches WHERE batch_id = ?", (b_id,))
    b_row = cur.fetchone()
    assert b_row["status"] == "quarantined"
    assert b_row["last_http_code"] == 400
    
    # Verify minute_readings are quarantined
    cur.execute("SELECT status FROM minute_readings WHERE batch_id = ?", (b_id,))
    for r in cur.fetchall():
        assert r["status"] == "quarantined"

def test_dispatcher_409_conflict_quarantines_batch(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 409
    mock_resp.json.return_value = {"status": "error", "message": "Conflict duplicate checksum"}
    mock_http.post.return_value = mock_resp
    
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM batches LIMIT 1")
    b_id = cur.fetchone()[0]
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "quarantined"
    
    cur.execute("SELECT status, last_http_code FROM batches WHERE batch_id = ?", (b_id,))
    b_row = cur.fetchone()
    assert b_row["status"] == "quarantined"
    assert b_row["last_http_code"] == 409

def test_dispatcher_429_rate_limiting_and_cooldown(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {"Retry-After": "45"}
    mock_resp.json.return_value = {"status": "error", "message": "Too Many Requests"}
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "rate_limited"
    
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    cool_row = cur.fetchone()
    assert cool_row is not None
    cooldown_until = cool_row[0]
    
    # Subsequent dispatch returns cooldown status
    mock_http.reset_mock()
    res_cool = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res_cool["status"] == "cooldown"
    assert res_cool["until"] == cooldown_until
    mock_http.post.assert_not_called()

def test_dispatcher_5xx_transient_error_backoff(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.json.return_value = {"status": "error", "message": "Service Unavailable"}
    mock_http.post.return_value = mock_resp
    
    res1 = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res1["status"] == "transient_error"
    assert res1["cooldown_sec"] >= 5
    
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'consecutive_transient_failures'")
    assert cur.fetchone()[0] == "1"
    
    # Clear cooldown to simulate waiting for next attempt
    cur.execute("DELETE FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    cur.execute("UPDATE batches SET next_retry_at = NULL")
    
    # 2nd failure
    res2 = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res2["status"] == "transient_error"
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'consecutive_transient_failures'")
    assert cur.fetchone()[0] == "2"

def test_dispatcher_network_exception_transient(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_http.post.side_effect = ConnectionError("Network unreachable")
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "transient_error"
    
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM batches LIMIT 1")
    b_id = cur.fetchone()[0]
    cur.execute("SELECT last_http_code, last_error_message FROM batches WHERE batch_id = ?", (b_id,))
    row = cur.fetchone()
    assert row["last_http_code"] == 0
    assert "Network unreachable" in row["last_error_message"]

def test_dispatcher_idle_when_no_pending(tmp_path):
    db_path = str(tmp_path / "test_empty.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    config = {
        "config_id": "cfg_test",
        "gateway_id": "gw_01",
        "api_endpoint": "http://mock-backend/api",
        "api_key": "test_key",
        "operational_stacks": []
    }
    mock_http = MagicMock()
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "idle"

def test_dispatcher_pacing_seconds(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM batches LIMIT 1")
    b_id = cur.fetchone()[0]
    mock_resp.json.return_value = {
        "status": "success", "batch_id": b_id, "processed_count": 2, "inserted_count": 2, "duplicated_count": 0
    }
    mock_http.post.return_value = mock_resp
    
    with patch("gateway.dispatcher.time.sleep") as mock_sleep:
        res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=1.5)
        assert res["status"] == "synced"
        mock_sleep.assert_called_once_with(1.5)

def test_parse_retry_after_formats():
    # Integer format
    assert parse_retry_after("120") == 120
    assert parse_retry_after(None, default_sec=30) == 30
    assert parse_retry_after("invalid_str", default_sec=30) == 30
    
    # HTTP Date format
    future_time = datetime.now(timezone.utc) + timedelta(seconds=90)
    http_date_str = format_datetime(future_time, usegmt=True)
    parsed = parse_retry_after(http_date_str)
    assert 85 <= parsed <= 95

def test_validate_ack_edge_cases():
    batch = {"batch_id": "b_123"}
    # Valid
    valid_resp = {
        "status": "success",
        "batch_id": "b_123",
        "processed_count": 3,
        "inserted_count": 2,
        "duplicated_count": 1
    }
    assert validate_ack(batch, valid_resp, 3) is True
    
    # Non-dict
    assert validate_ack(batch, "string_response", 3) is False
    
    # Wrong status
    assert validate_ack(batch, {**valid_resp, "status": "failed"}, 3) is False
    
    # Batch ID mismatch
    assert validate_ack(batch, {**valid_resp, "batch_id": "b_wrong"}, 3) is False
    
    # Count mismatch
    assert validate_ack(batch, {**valid_resp, "processed_count": 2}, 3) is False
    assert validate_ack(batch, {**valid_resp, "inserted_count": 1, "duplicated_count": 1}, 3) is False

def test_dispatcher_corrupt_payload_json_quarantined(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM batches LIMIT 1")
    b_id = cur.fetchone()[0]
    
    # Intentionally corrupt payload_json in database
    cur.execute("UPDATE batches SET payload_json = 'INVALID_JSON{{' WHERE batch_id = ?", (b_id,))
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "quarantined"
    
    # HTTP should NOT have been called
    mock_http.post.assert_not_called()
    
    # Verify batch status and last_error_message
    cur.execute("SELECT status, last_error_message FROM batches WHERE batch_id = ?", (b_id,))
    batch_row = cur.fetchone()
    assert batch_row["status"] == "quarantined"
    assert "CORRUPT_PAYLOAD_JSON" in batch_row["last_error_message"]
    
    # Verify minute_readings status
    cur.execute("SELECT status FROM minute_readings WHERE batch_id = ?", (b_id,))
    readings = cur.fetchall()
    assert len(readings) > 0
    for r in readings:
        assert r["status"] == "quarantined"
