# tests/test_gap_backfill.py
import sqlite3
from datetime import datetime, timezone, timedelta
from gateway.models import init_db, get_db_connection
from gateway.aggregator import startup_gap_backfill

def test_startup_gap_backfill_exact_181_minutes(tmp_path):
    db_path = str(tmp_path / "test_cems.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    # 1. Giả lập bản ghi cuối cùng trong DB là 09:59:00.000Z
    cur = conn.cursor()
    last_ts = "2026-09-18T09:59:00.000Z"
    cur.execute("""
        INSERT INTO minute_readings (
            stack_id, device_id, measurement_config_id, minute_ts, coverage_status,
            valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json,
            status, created_at
        ) VALUES ('stack_01', 'node_01', 'cfg_test', ?, 'full', '{}', '{}', 'in_control', '{}', 'synced', ?)
    """, (last_ts, last_ts))
    conn.commit()
    
    config = {
        "config_id": "cfg_test",
        "operational_stacks": [
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": "2026-09-18T00:00:00.000Z"}
        ]
    }
    
    # 2. Giả lập Gateway khởi động lại tại mốc 13:02:00.000Z
    # Cutoff = 13:02:00Z - 120s = 13:00:00.000Z
    # Khoảng bù: từ 10:00:00Z đến 13:00:00Z tính cả hai đầu = 181 phút
    mock_now_dt = datetime.fromisoformat("2026-09-18T13:02:00.000+00:00")
    
    inserted = startup_gap_backfill(conn, config, current_time_dt=mock_now_dt)
    assert inserted == 181
    
    cur.execute("SELECT count(*) FROM minute_readings WHERE gap_reason = 'gateway_offline_backfill'")
    assert cur.fetchone()[0] == 181
    
    # Kiểm tra phút đầu và phút cuối được backfill
    cur.execute("SELECT min(minute_ts), max(minute_ts) FROM minute_readings WHERE gap_reason = 'gateway_offline_backfill'")
    min_ts, max_ts = cur.fetchone()
    assert min_ts == "2026-09-18T10:00:00.000Z"
    assert max_ts == "2026-09-18T13:00:00.000Z"
    conn.close()

def test_startup_gap_backfill_inactive_stack_ignored(tmp_path):
    db_path = str(tmp_path / "test_cems_inactive.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    config = {
        "config_id": "cfg_test",
        "operational_stacks": [
            {"stack_id": "stack_inactive", "device_id": "node_02", "is_active": False, "active_from": "2026-09-18T00:00:00.000Z"}
        ]
    }
    mock_now_dt = datetime.fromisoformat("2026-09-18T12:00:00.000+00:00")
    inserted = startup_gap_backfill(conn, config, current_time_dt=mock_now_dt)
    assert inserted == 0
    
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM minute_readings")
    assert cur.fetchone()[0] == 0
    conn.close()

def test_startup_gap_backfill_24h_cap(tmp_path):
    db_path = str(tmp_path / "test_cems_24h.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    # Giả lập bản ghi cuối cùng từ 3 ngày trước (72 giờ)
    cur = conn.cursor()
    last_ts = "2026-09-15T12:00:00.000Z"
    cur.execute("""
        INSERT INTO minute_readings (
            stack_id, device_id, measurement_config_id, minute_ts, coverage_status,
            valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json,
            status, created_at
        ) VALUES ('stack_01', 'node_01', 'cfg_test', ?, 'full', '{}', '{}', 'in_control', '{}', 'synced', ?)
    """, (last_ts, last_ts))
    conn.commit()
    
    config = {
        "config_id": "cfg_test",
        "operational_stacks": [
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": "2026-09-10T00:00:00.000Z"}
        ]
    }
    
    # now = 2026-09-18T12:02:00.000Z
    # effective_end_dt = 2026-09-18T12:02:00Z
    # max_backfill_dt = 2026-09-17T12:02:00Z
    # cutoff_dt = 2026-09-18T12:00:00Z
    # start_dt capped to max_backfill_dt (2026-09-17T12:02:00Z)
    # Range is from 2026-09-17T12:02:00Z to 2026-09-18T12:00:00Z -> 24h - 2 min = 1438 + 1 = 1439 minutes
    mock_now_dt = datetime.fromisoformat("2026-09-18T12:02:00.000+00:00")
    inserted = startup_gap_backfill(conn, config, current_time_dt=mock_now_dt)
    
    # 24 hours is at most 1440 minutes, with 2m cutoff it's 1439 minutes
    assert inserted == 1439
    
    cur.execute("SELECT min(minute_ts), max(minute_ts) FROM minute_readings WHERE gap_reason = 'gateway_offline_backfill'")
    min_ts, max_ts = cur.fetchone()
    assert min_ts == "2026-09-17T12:02:00.000Z"
    assert max_ts == "2026-09-18T12:00:00.000Z"
    conn.close()

def test_startup_gap_backfill_idempotent(tmp_path):
    db_path = str(tmp_path / "test_cems_idempotent.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    config = {
        "config_id": "cfg_test",
        "operational_stacks": [
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": "2026-09-18T11:55:00.000Z"}
        ]
    }
    mock_now_dt = datetime.fromisoformat("2026-09-18T12:02:00.000+00:00")
    
    # First run
    inserted_1 = startup_gap_backfill(conn, config, current_time_dt=mock_now_dt)
    assert inserted_1 == 6  # 11:55 to 12:00 inclusive = 6 minutes
    
    # Second run immediately after
    inserted_2 = startup_gap_backfill(conn, config, current_time_dt=mock_now_dt)
    assert inserted_2 == 0
    conn.close()

def test_startup_gap_backfill_active_until(tmp_path):
    db_path = str(tmp_path / "test_cems_until.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    config = {
        "config_id": "cfg_test",
        "operational_stacks": [
            {
                "stack_id": "stack_01",
                "device_id": "node_01",
                "is_active": True,
                "active_from": "2026-09-18T10:00:00.000Z",
                "active_until": "2026-09-18T11:02:00.000Z"
            }
        ]
    }
    # now is 13:00, but active_until is 11:02:00
    # effective_end_dt is 11:02:00Z -> cutoff is 11:00:00Z
    # Range: 10:00 to 11:00 inclusive = 61 minutes
    mock_now_dt = datetime.fromisoformat("2026-09-18T13:00:00.000+00:00")
    inserted = startup_gap_backfill(conn, config, current_time_dt=mock_now_dt)
    assert inserted == 61
    
    cur = conn.cursor()
    cur.execute("SELECT min(minute_ts), max(minute_ts) FROM minute_readings WHERE gap_reason = 'gateway_offline_backfill'")
    min_ts, max_ts = cur.fetchone()
    assert min_ts == "2026-09-18T10:00:00.000Z"
    assert max_ts == "2026-09-18T11:00:00.000Z"
    conn.close()

def test_startup_gap_backfill_clears_seconds_and_microseconds(tmp_path):
    db_path = str(tmp_path / "test_cems_subsecond.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    config = {
        "config_id": "cfg_test",
        "operational_stacks": [
            {
                "stack_id": "stack_01",
                "device_id": "node_01",
                "is_active": True,
                "active_from": "2026-09-18T11:55:34.567Z"
            }
        ]
    }
    mock_now_dt = datetime.fromisoformat("2026-09-18T12:02:45.123+00:00")
    inserted = startup_gap_backfill(conn, config, current_time_dt=mock_now_dt)
    assert inserted == 6
    
    cur = conn.cursor()
    cur.execute("SELECT minute_ts FROM minute_readings ORDER BY minute_ts")
    rows = [r[0] for r in cur.fetchall()]
    assert rows[0] == "2026-09-18T11:55:00.000Z"
    assert rows[-1] == "2026-09-18T12:00:00.000Z"
    conn.close()
