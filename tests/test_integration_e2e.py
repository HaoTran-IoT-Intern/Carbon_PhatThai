# tests/test_integration_e2e.py
import pytest
import json
import hashlib
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from gateway.models import init_db, get_db_connection
from gateway.aggregator import startup_gap_backfill, recompute_minute_rollup
from gateway.slot_manager import ingest_raw_sample
from gateway.batch_manager import freeze_and_create_batch
from gateway.dispatcher import dispatch_single_batch, resume_suspended_stack, resume_dispatcher
from gateway.retention import evaluate_emergency_freeze, check_disk_watermark
from gateway.server import app, reset_mock_server_state, FlaskTestClientAdapter, batch_db, readings_db


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "e2e.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    config = {
        "config_id": "cfg_cement_stack_v1",
        "gateway_id": "gw_01",
        "api_endpoint": "http://mock-be/api/v1/cems/telemetry/batch",
        "api_key": "valid_key",
        "operational_stacks": [
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": "2026-09-18T00:00:00.000Z"}
        ]
    }
    reset_mock_server_state()
    return conn, config, db_path


# ==============================================================================
# Test 1 & Test 2: Happy Path & Idempotency SHA-256 (Brief Reference Specification)
# ==============================================================================
def test_test1_and_test2_happy_path_and_idempotency(env):
    conn, config, _ = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T10:00:00.000Z"
    for s in range(0, 60, 10):
        ingest_raw_sample(conn, {
            "device_id": "node_01", "boot_id": "b1", "sample_seq": s // 10,
            "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
            "sample_ts": f"2026-09-18T10:00:{s:02d}.000Z", "time_quality": "ntp_synced",
            "co2_pct": 14.0, "flue_flow_m3h": 2000.0,
            "flue_temp_c": 150.0, "flue_pressure_gauge_kpa": 1.2, "raw_payload_text": f"p_{s}"
        }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T10:05:00.000+00:00")
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1, current_time_dt=mock_freeze_dt)
    assert b_id is not None
    
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "success", "batch_id": b_id, "processed_count": 1, "inserted_count": 1, "duplicated_count": 0}
    mock_http.post.return_value = mock_resp
    
    # Test 1: Happy Path
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "synced"
    
    # Test 2: Idempotency (gửi lại nhận duplicate)
    mock_resp.json.return_value = {"status": "success", "batch_id": b_id, "processed_count": 1, "inserted_count": 0, "duplicated_count": 1}
    cur.execute("UPDATE batches SET status = 'pending' WHERE batch_id = ?", (b_id,))
    res_dup = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res_dup["status"] == "synced"


# ==============================================================================
# Test 1 & 2 (Live E2E with Mock Backend Flask Server): Two-tier Idempotency & SHA-256
# ==============================================================================
def test_test1_and_test2_live_flask_server_idempotency(env):
    conn, config, _ = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T10:00:00.000Z"
    for s in range(0, 60, 10):
        ingest_raw_sample(conn, {
            "device_id": "node_01", "boot_id": "b_live", "sample_seq": s // 10,
            "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
            "sample_ts": f"2026-09-18T10:00:{s:02d}.000Z", "time_quality": "ntp_synced",
            "co2_pct": 14.5, "flue_flow_m3h": 2100.0,
            "flue_temp_c": 152.0, "flue_pressure_gauge_kpa": 1.25, "raw_payload_text": f"live_p_{s}"
        }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T10:05:00.000+00:00")
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1, current_time_dt=mock_freeze_dt)
    assert b_id is not None

    # Connect dispatcher directly to Flask test client via adapter
    server_client = FlaskTestClientAdapter(app.test_client())
    live_config = dict(config)
    live_config["api_key"] = "cems_gw_secret_key_prod_xxxxxxxxx"
    
    # Test 1: First dispatch -> Backend inserts 1 reading
    res = dispatch_single_batch(conn, server_client, live_config, pacing_seconds=0)
    assert res["status"] == "synced"
    assert b_id in batch_db
    assert len(readings_db) == 1
    
    # Test 2: Resend same batch -> Backend returns 200 with duplicated_count=1, inserted_count=0
    cur.execute("UPDATE batches SET status = 'pending' WHERE batch_id = ?", (b_id,))
    res_dup = dispatch_single_batch(conn, server_client, live_config, pacing_seconds=0)
    assert res_dup["status"] == "synced"
    assert len(readings_db) == 1  # Không bị duplicate bản ghi trong CSDL Backend


# ==============================================================================
# Test 3: Conflict Test (409 Quarantined)
# ==============================================================================
def test_test3_conflict_409_quarantine(env):
    conn, config, _ = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T09:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 1,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sample_1"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T09:05:00.000+00:00")
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1, current_time_dt=mock_freeze_dt)
    assert b_id is not None
    
    # 1. Mock 409
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 409
    mock_resp.json.return_value = {"status": "error", "message": "Payload conflict with existing batch"}
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "quarantined"
    
    cur.execute("SELECT status, last_http_code FROM batches WHERE batch_id = ?", (b_id,))
    st, code = cur.fetchone()
    assert st == "quarantined"
    assert code == 409

    # 2. Live Mock Backend Server 409: Cố tình gửi sai SHA-256 cho batch_id đã tồn tại
    live_config = dict(config)
    live_config["api_key"] = "cems_gw_secret_key_prod_xxxxxxxxx"
    server_client = FlaskTestClientAdapter(app.test_client())
    
    # Giả lập batch_id đã tồn tại trên server nhưng với hash khác
    batch_db[b_id] = {"payload_hash": "different_sha256_hash_on_server"}
    cur.execute("UPDATE batches SET status = 'pending' WHERE batch_id = ?", (b_id,))
    res_server_conflict = dispatch_single_batch(conn, server_client, live_config, pacing_seconds=0)
    assert res_server_conflict["status"] == "quarantined"


# ==============================================================================
# Test 4: Atomic Batch Rollback (500 Server Error)
# ==============================================================================
def test_test4_atomic_rollback_on_500(env):
    conn, config, _ = env
    cur = conn.cursor()
    
    # Tạo batch 5 phút gồm 5 bản ghi 1-phút (từ 08:00 đến 08:04)
    for m in range(5):
        m_ts = f"2026-09-18T08:0{m}:00.000Z"
        ingest_raw_sample(conn, {
            "device_id": "node_01", "boot_id": "b1", "sample_seq": 20 + m,
            "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
            "sample_ts": m_ts, "raw_payload_text": f"sample_2_{m}"
        }, config)
        recompute_minute_rollup(conn, "stack_01", m_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T08:10:00.000+00:00")
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5, current_time_dt=mock_freeze_dt)
    assert b_id is not None
    cur.execute("SELECT readings_count FROM batches WHERE batch_id = ?", (b_id,))
    assert cur.fetchone()[0] == 5
    
    # 1. Mock 500: Server giả lập lỗi chung
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"status": "error", "message": "Database rollback"}
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "transient_error"
    cur.execute("SELECT status FROM batches WHERE batch_id = ?", (b_id,))
    assert cur.fetchone()[0] == "pending"

    # 2. Live Server Mid-Batch Rollback: Lỗi DB xảy ra tại bản ghi thứ 3 trong 5 bản ghi (index 2)
    live_config = dict(config)
    live_config["api_key"] = "cems_gw_secret_key_prod_xxxxxxxxx"
    server_client = FlaskTestClientAdapter(app.test_client())
    
    cur.execute("DELETE FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    cur.execute("UPDATE batches SET next_retry_at = NULL WHERE batch_id = ?", (b_id,))
    
    # Cấu hình server kích hoạt lỗi DB ở bản ghi thứ 3 (index 2) sau khi đã insert 2 bản ghi đầu
    app.config["SIMULATE_FAIL_ON_READING_INDEX"] = 2
    try:
        res_live_500 = dispatch_single_batch(conn, server_client, live_config, pacing_seconds=0)
        assert res_live_500["status"] == "transient_error"
        # Xác nhận toàn bộ transaction bị rollback: 2 bản ghi đầu bị xóa khỏi DB, 0 bản ghi lưu lại
        assert len(readings_db) == 0
        assert b_id not in batch_db
        
        # Gateway giữ nguyên batch ở trạng thái pending và ghi nhận mã lỗi 500
        cur.execute("SELECT status, last_http_code FROM batches WHERE batch_id = ?", (b_id,))
        st, code = cur.fetchone()
        assert st == "pending"
        assert code == 500
        
        # Gateway kích hoạt cooldown toàn trạm
        cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
        assert cur.fetchone()[0] is not None
    finally:
        app.config["SIMULATE_FAIL_ON_READING_INDEX"] = None

    # 3. Kiểm tra kiểm soát Schema: Gửi payload gốc không phải dict (ví dụ: list) trả về 400 Bad Request
    client = app.test_client()
    resp_list_root = client.post(
        "/api/v1/cems/telemetry/batch",
        headers={"Content-Type": "application/json", "X-API-Key": "cems_gw_secret_key_prod_xxxxxxxxx"},
        data="[1, 2, 3]"
    )
    assert resp_list_root.status_code == 400
    assert resp_list_root.get_json()["status"] == "error"


# ==============================================================================
# Test 5: ACK Loss Recovery
# ==============================================================================
def test_test5_ack_loss_recovery(env):
    conn, config, _ = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T07:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 3,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sample_3"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T07:05:00.000+00:00")
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1, current_time_dt=mock_freeze_dt)
    assert b_id is not None
    
    mock_http = MagicMock()
    # Lần 1: Mất mạng ngay trước khi nhận ACK
    mock_http.post.side_effect = Exception("Connection reset by peer")
    dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    
    cur.execute("SELECT status, attempt_count FROM batches WHERE batch_id = ?", (b_id,))
    row = cur.fetchone()
    assert row["attempt_count"] == 1
    
    # Lần 2: Retry nhận duplicate 200 OK
    mock_http.post.side_effect = None
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "success", "batch_id": b_id, "processed_count": 1, "inserted_count": 0, "duplicated_count": 1}
    mock_http.post.return_value = mock_resp
    
    # Reset cooldown để retry ngay
    cur.execute("DELETE FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    cur.execute("UPDATE batches SET next_retry_at = NULL WHERE batch_id = ?", (b_id,))
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "synced"


# ==============================================================================
# Test 6: Gateway Crash & Restart Recovery (Spec Section 8, Test 6)
# ==============================================================================
def test_test6_gateway_crash_and_restart_recovery(env):
    conn, config, db_path = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T06:30:00.000Z"
    for s in range(0, 60, 10):
        ingest_raw_sample(conn, {
            "device_id": "node_01", "boot_id": "b_crash", "sample_seq": s // 10,
            "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
            "sample_ts": f"2026-09-18T06:30:{s:02d}.000Z", "time_quality": "ntp_synced",
            "co2_pct": 13.5, "flue_flow_m3h": 1900.0,
            "flue_temp_c": 149.0, "flue_pressure_gauge_kpa": 1.1, "raw_payload_text": f"crash_{s}"
        }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T06:35:00.000+00:00")
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1, current_time_dt=mock_freeze_dt)
    assert b_id is not None
    
    # Giả lập Gateway đang có batch pending và cooldown trong gateway_system_state
    future_cooldown = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_cooldown_until', ?, ?)",
                (future_cooldown, "2026-09-18T06:31:00.000Z"))
    conn.commit()
    
    # Giả lập SIGKILL: Đóng kết nối hiện tại đột ngột
    conn.close()
    
    # Khởi động lại Gateway: Mở kết nối SQLite mới từ đĩa
    new_conn = get_db_connection(db_path)
    mock_http = MagicMock()
    
    # 1. Dispatcher nạp lại trạng thái từ CSDL, tôn trọng dispatcher_cooldown_until -> không gửi vội
    res_cool = dispatch_single_batch(new_conn, mock_http, config, pacing_seconds=0)
    assert res_cool["status"] == "cooldown"
    assert mock_http.post.call_count == 0
    
    # 2. Sau khi cooldown hết hiệu lực -> Dispatcher gửi thành công, không mất mát batch
    new_cur = new_conn.cursor()
    new_cur.execute("DELETE FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    new_conn.commit()
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "success", "batch_id": b_id, "processed_count": 1, "inserted_count": 1, "duplicated_count": 0}
    mock_http.post.return_value = mock_resp
    
    res_sync = dispatch_single_batch(new_conn, mock_http, config, pacing_seconds=0)
    assert res_sync["status"] == "synced"
    assert mock_http.post.call_count == 1
    
    new_cur.execute("SELECT status FROM batches WHERE batch_id = ?", (b_id,))
    assert new_cur.fetchone()[0] == "synced"
    new_conn.close()


# ==============================================================================
# Test 7: Mẫu đến muộn khi đã Batched / Synced
# ==============================================================================
def test_test7_late_sample_after_freeze(env):
    conn, config, _ = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T06:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 4,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sample_4"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T06:05:00.000+00:00")
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1, current_time_dt=mock_freeze_dt)
    assert b_id is not None
    
    # Gửi bù mẫu cho phút đã batched này
    res = ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 999,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T06:00:10.000Z", "raw_payload_text": "sample_late"
    }, config)
    assert res["status"] == "accepted_late_frozen"
    
    cur.execute("SELECT has_unreconciled_late_data FROM minute_readings WHERE minute_ts = ?", (minute_ts,))
    assert cur.fetchone()[0] == 1
    
    # Kiểm tra raw_samples được đánh dấu superseded và late level 2
    cur.execute("SELECT is_superseded, is_late FROM raw_samples WHERE sample_seq = 999")
    is_sup, is_late = cur.fetchone()
    assert is_sup == 1
    assert is_late == 2


# ==============================================================================
# Test 8: Slot Collision & Invalid Time
# ==============================================================================
def test_test8_slot_collision_and_invalid_time(env):
    conn, config, _ = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T05:00:00.000Z"
    
    # 1. 2 mẫu tranh chấp slot 0: ntp_synced thắng gateway_assigned
    sample_gw = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 10,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T05:00:02.000Z", "time_quality": "gateway_assigned",
        "arrived_at": "2026-09-18T05:00:03.000Z",
        "raw_payload_text": "s_gw"
    }
    sample_ntp = {
        "device_id": "node_01", "boot_id": "b2", "sample_seq": 1,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T05:00:05.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T05:00:05.500Z",
        "raw_payload_text": "s_ntp"
    }
    ingest_raw_sample(conn, sample_gw, config)
    ingest_raw_sample(conn, sample_ntp, config)
    
    cur.execute("SELECT boot_id, is_superseded FROM raw_samples WHERE stack_id = 'stack_01' AND minute_window_ts = ?", (minute_ts,))
    rows = dict(cur.fetchall())
    assert rows["b1"] == 1
    assert rows["b2"] == 0

    # 2. Mẫu có time_quality = 'invalid' và chuỗi sample_ts = 'invalid_ts'
    sample_invalid = {
        "device_id": "node_01", "boot_id": "b3", "sample_seq": 1,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "invalid_ts", "time_quality": "invalid",
        "raw_payload_text": "s_invalid"
    }
    res_inv = ingest_raw_sample(conn, sample_invalid, config)
    assert res_inv["status"] == "accepted_invalid_time"
    
    cur.execute("SELECT slot_index, minute_window_ts, is_superseded, is_late FROM raw_samples WHERE boot_id = 'b3'")
    slot_idx, win_ts, is_sup, is_late = cur.fetchone()
    assert slot_idx is None
    assert win_ts is None
    assert is_sup == 1
    assert is_late == 3


# ==============================================================================
# Test 9: 181 Gap Minutes from 10:00Z to 13:00Z
# ==============================================================================
def test_test9_181_gap_minutes(env):
    conn, config, _ = env
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO minute_readings (
            stack_id, device_id, measurement_config_id, minute_ts, coverage_status,
            valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json,
            status, created_at
        ) VALUES ('stack_01', 'node_01', 'cfg_cement_stack_v1', '2026-09-18T09:59:00.000Z', 'full', '{}', '{}', 'in_control', '{}', 'synced', '2026-09-18T09:59:00.000Z')
    """)
    conn.commit()
    
    mock_now = datetime.fromisoformat("2026-09-18T13:02:00.000+00:00")
    backfilled = startup_gap_backfill(conn, config, current_time_dt=mock_now)
    assert backfilled == 181
    
    # Kiểm tra gap_reason và coverage_status
    cur.execute("SELECT COUNT(*) FROM minute_readings WHERE gap_reason = 'gateway_offline_backfill' AND coverage_status = 'none'")
    assert cur.fetchone()[0] == 181
    
    # Kiểm tra biên mốc thời gian bắt đầu và kết thúc bù
    cur.execute("SELECT min(minute_ts), max(minute_ts) FROM minute_readings WHERE gap_reason = 'gateway_offline_backfill'")
    min_ts, max_ts = cur.fetchone()
    assert min_ts == "2026-09-18T10:00:00.000Z"
    assert max_ts == "2026-09-18T13:00:00.000Z"


# ==============================================================================
# Test 10: Security Errors (401 Halt, 403 Stack Suspension, 429 Retry-After, 503 Cooldown)
# ==============================================================================
def test_test10_security_errors(env):
    conn, config, _ = env
    cur = conn.cursor()
    
    minute_ts = "2026-09-18T04:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b10", "sample_seq": 1,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sec_sample"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T04:05:00.000+00:00")
    b_id1 = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1, current_time_dt=mock_freeze_dt)
    assert b_id1 is not None
    
    # --- 1. 401 Unauthorized ---
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"error": "Unauthorized"}
    mock_http.post.return_value = mock_resp
    
    res401 = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res401["status"] == "halted"
    
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_halted'")
    assert cur.fetchone()[0] == "1"
    
    # Resume dispatcher
    resume_dispatcher(conn)
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_halted'")
    assert cur.fetchone() is None
    
    # --- 2. 403 Forbidden (Per-stack suspension) ---
    mock_resp.status_code = 403
    mock_resp.json.return_value = {"error": "Forbidden for stack_01"}
    mock_http.post.return_value = mock_resp
    
    res403 = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res403["status"] == "stack_suspended"
    
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'stack_suspended:stack_01'")
    assert cur.fetchone()[0] == "403_FORBIDDEN"
    
    # Resume suspended stack
    resume_suspended_stack(conn, "stack_01")
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'stack_suspended:stack_01'")
    assert cur.fetchone() is None

    # --- 3. 429 Rate Limiting with Retry-After header ---
    mock_resp.status_code = 429
    mock_resp.headers = {"Retry-After": "15"}
    mock_resp.json.return_value = {"error": "Rate limit exceeded"}
    mock_http.post.return_value = mock_resp
    
    res429 = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res429["status"] == "rate_limited"
    
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    cool_val = cur.fetchone()[0]
    assert cool_val is not None
    
    # Clean cooldown
    cur.execute("DELETE FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    conn.commit()

    # --- 4. 503 Transient Failure with Jitter Backoff ---
    mock_resp.status_code = 503
    mock_resp.headers = {}
    mock_resp.json.return_value = {"error": "Service Unavailable"}
    mock_http.post.return_value = mock_resp
    
    res503 = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res503["status"] == "transient_error"
    assert res503["cooldown_sec"] >= 5
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'consecutive_transient_failures'")
    assert int(cur.fetchone()[0]) >= 1


# ==============================================================================
# Test 11: Emergency Freeze & Hysteresis Logic
# ==============================================================================
def test_test11_emergency_freeze_hysteresis():
    state = "NORMAL"
    # Giảm xuống 4.5% -> FREEZE (< 5%)
    state = evaluate_emergency_freeze(4.5, state)
    assert state == "FREEZE"
    # Tăng lên 7.0% -> Vẫn FREEZE (Hysteresis giữa 5% và 8%)
    state = evaluate_emergency_freeze(7.0, state)
    assert state == "FREEZE"
    # Tăng lên 8.5% -> Phục hồi NORMAL (> 8%)
    state = evaluate_emergency_freeze(8.5, state)
    assert state == "NORMAL"


def test_mock_server_strict_validation():
    from gateway.server import app, VALID_API_KEY
    client = app.test_client()
    headers = {"X-API-Key": VALID_API_KEY}

    # 1. Missing or non-dict JSON body
    resp = client.post("/api/v1/cems/telemetry/batch", headers=headers, data="")
    assert resp.status_code == 400
    assert "error" in resp.get_json()["status"]

    # 2. Invalid schema_version
    bad_ver = {
        "schema_version": "1.0.0",
        "batch_id": "b1",
        "gateway_id": "gw_01",
        "batch_created_at": "2026-09-18T10:00:00.000Z",
        "readings": [{"stack_id": "s1"}]
    }
    resp = client.post("/api/v1/cems/telemetry/batch", headers=headers, json=bad_ver)
    assert resp.status_code == 400
    assert "schema_version" in resp.get_json()["message"]

    # 3. Empty readings list
    empty_readings = {
        "schema_version": "1.2.3",
        "batch_id": "b1",
        "gateway_id": "gw_01",
        "batch_created_at": "2026-09-18T10:00:00.000Z",
        "readings": []
    }
    resp = client.post("/api/v1/cems/telemetry/batch", headers=headers, json=empty_readings)
    assert resp.status_code == 400
    assert "Readings count" in resp.get_json()["message"]

    # 4. Missing required field in reading (missing 'samples')
    incomplete_reading = {
        "schema_version": "1.2.3",
        "batch_id": "b1",
        "gateway_id": "gw_01",
        "batch_created_at": "2026-09-18T10:00:00.000Z",
        "readings": [{
            "stack_id": "stack_01",
            "device_id": "dev_01",
            "measurement_config_id": "cfg_01",
            "ts": "2026-09-18T10:00:00.000Z",
            "window_seconds": 60,
            # 'samples' missing!
            "gases_and_dust": {},
            "flue_gas": {},
            "ambient": {},
            "integrated_emissions": {},
            "qaqc": {}
        }]
    }
    resp = client.post("/api/v1/cems/telemetry/batch", headers=headers, json=incomplete_reading)
    assert resp.status_code == 400
    assert "missing required field" in resp.get_json()["message"].lower()

