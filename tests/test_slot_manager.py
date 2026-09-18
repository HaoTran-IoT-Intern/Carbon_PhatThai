import pytest
import json
from datetime import datetime, timezone, timedelta
from gateway.models import init_db, get_db_connection
from gateway.slot_manager import compare_slot_priority, ingest_raw_sample

@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test_cems.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    yield conn
    conn.close()

def test_slot_priority_ntp_beats_gateway_assigned():
    sample_ntp = {"time_quality": "ntp_synced", "is_calibrating": 0, "arrived_at": "2026-09-18T10:00:05.100Z"}
    sample_gw = {"time_quality": "gateway_assigned", "is_calibrating": 0, "arrived_at": "2026-09-18T10:00:05.000Z"}
    assert compare_slot_priority(sample_ntp, sample_gw) > 0
    assert compare_slot_priority(sample_gw, sample_ntp) < 0

def test_slot_priority_core_completeness():
    sample_full = {
        "time_quality": "ntp_synced",
        "co2_pct": 12.5, "flue_flow_m3h": 1500.0, "flue_temp_c": 120.0, "flue_pressure_gauge_kpa": 1.0,
        "is_calibrating": 0, "arrived_at": "2026-09-18T10:00:05.000Z"
    }
    sample_partial = {
        "time_quality": "ntp_synced",
        "co2_pct": 12.5, "flue_flow_m3h": None, "flue_temp_c": 120.0, "flue_pressure_gauge_kpa": 1.0,
        "is_calibrating": 0, "arrived_at": "2026-09-18T10:00:04.000Z"
    }
    assert compare_slot_priority(sample_full, sample_partial) > 0
    assert compare_slot_priority(sample_partial, sample_full) < 0

def test_slot_priority_calibration_and_arrival():
    sample_normal = {
        "time_quality": "ntp_synced",
        "is_calibrating": 0,
        "arrived_at": "2026-09-18T10:00:06.000Z"
    }
    sample_calibrating = {
        "time_quality": "ntp_synced",
        "is_calibrating": 1,
        "arrived_at": "2026-09-18T10:00:04.000Z"
    }
    assert compare_slot_priority(sample_normal, sample_calibrating) > 0

    sample_earlier = {
        "time_quality": "ntp_synced",
        "is_calibrating": 0,
        "arrived_at": "2026-09-18T10:00:04.000Z"
    }
    assert compare_slot_priority(sample_earlier, sample_normal) > 0
    assert compare_slot_priority(sample_normal, sample_earlier) < 0
    assert compare_slot_priority(sample_normal, sample_normal) == 0

def test_frozen_minute_protection_marks_late_sample_superseded(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T10:00:00.000Z"
    
    # Tạo sẵn một minute_reading với status 'batched'
    cur = db_conn.cursor()
    cur.execute("""
        INSERT INTO minute_readings (
            stack_id, device_id, measurement_config_id, minute_ts, coverage_status,
            valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json,
            status, created_at
        ) VALUES ('stack_01', 'node_01', 'cfg_test', ?, 'full', '{}', '{}', 'in_control', '{}', 'batched', ?)
    """, (minute_ts, minute_ts))
    db_conn.commit()
    
    # Ingest mẫu đến muộn thuộc về phút đã đóng băng
    sample_late = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 99,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:20.000Z", "time_quality": "ntp_synced",
        "minute_window_ts": minute_ts, "co2_pct": 14.5, "flue_flow_m3h": 2000.0,
        "flue_temp_c": 150.0, "flue_pressure_gauge_kpa": 1.2,
        "raw_payload_text": "payload_late"
    }
    res = ingest_raw_sample(db_conn, sample_late, config)
    assert res["status"] == "accepted_late_frozen"
    
    # Kiểm tra raw_samples: is_superseded = 1, is_late = 2
    cur.execute("SELECT is_superseded, is_late FROM raw_samples WHERE sample_seq = 99")
    r = cur.fetchone()
    assert r["is_superseded"] == 1
    assert r["is_late"] == 2
    
    # Kiểm tra minute_readings: has_unreconciled_late_data = 1
    cur.execute("SELECT has_unreconciled_late_data FROM minute_readings WHERE minute_ts = ?", (minute_ts,))
    assert cur.fetchone()["has_unreconciled_late_data"] == 1

def test_invalid_time_sample_handling(db_conn):
    config = {"config_id": "cfg_test"}
    sample_invalid = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 101,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "invalid_ts_string", "time_quality": "invalid",
        "raw_payload_text": "payload_invalid"
    }
    res = ingest_raw_sample(db_conn, sample_invalid, config)
    assert res["status"] == "accepted_invalid_time"
    
    cur = db_conn.cursor()
    cur.execute("SELECT slot_index, minute_window_ts, is_superseded, is_late FROM raw_samples WHERE sample_seq = 101")
    r = cur.fetchone()
    assert r["slot_index"] is None
    assert r["minute_window_ts"] is None
    assert r["is_superseded"] == 1
    assert r["is_late"] == 3

def test_duplicate_raw_sample_ignored(db_conn):
    config = {"config_id": "cfg_test"}
    sample = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 10,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:10.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:10.500Z",
        "raw_payload_text": "same_payload"
    }
    res1 = ingest_raw_sample(db_conn, sample, config)
    assert res1["status"] == "accepted"

    res2 = ingest_raw_sample(db_conn, sample, config)
    assert res2["status"] == "duplicate_ignored"

    cur = db_conn.cursor()
    cur.execute("SELECT count(*) FROM raw_samples WHERE sample_seq = 10")
    assert cur.fetchone()[0] == 1

def test_conflicting_raw_sample_recorded_and_rejected(db_conn):
    config = {"config_id": "cfg_test"}
    sample1 = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 20,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:10.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:10.500Z",
        "raw_payload_text": "payload_v1"
    }
    sample2 = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 20,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:10.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:10.600Z",
        "raw_payload_text": "payload_v2_conflicting"
    }
    res1 = ingest_raw_sample(db_conn, sample1, config)
    assert res1["status"] == "accepted"

    res2 = ingest_raw_sample(db_conn, sample2, config)
    assert res2["status"] == "conflict_rejected"

    cur = db_conn.cursor()
    cur.execute("SELECT existing_payload, conflicting_payload FROM raw_ingest_conflicts WHERE sample_seq = 20")
    conflict = cur.fetchone()
    assert conflict is not None
    assert conflict["existing_payload"] == "payload_v1"
    assert conflict["conflicting_payload"] == "payload_v2_conflicting"

def test_slot_collision_resolution(db_conn):
    config = {"config_id": "cfg_test"}
    # Mẫu 1: slot 1 (giây 10), time_quality = gateway_assigned
    sample1 = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 31,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:12.000Z", "time_quality": "gateway_assigned",
        "arrived_at": "2026-09-18T10:00:12.100Z",
        "raw_payload_text": "sample_31"
    }
    # Mẫu 2: cùng slot 1 (giây 15), time_quality = ntp_synced -> thắng mẫu 1
    sample2 = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 32,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:15.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:15.200Z",
        "raw_payload_text": "sample_32"
    }
    # Mẫu 3: cùng slot 1 (giây 18), time_quality = gateway_assigned -> thua mẫu 2
    sample3 = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 33,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:18.000Z", "time_quality": "gateway_assigned",
        "arrived_at": "2026-09-18T10:00:18.300Z",
        "raw_payload_text": "sample_33"
    }

    ingest_raw_sample(db_conn, sample1, config)
    ingest_raw_sample(db_conn, sample2, config)
    ingest_raw_sample(db_conn, sample3, config)

    cur = db_conn.cursor()
    cur.execute("SELECT sample_seq, is_superseded FROM raw_samples WHERE slot_index = 1 ORDER BY sample_seq")
    rows = cur.fetchall()
    status_by_seq = {r["sample_seq"]: r["is_superseded"] for r in rows}

    assert status_by_seq[31] == 1  # Bị thay thế bởi mẫu 32
    assert status_by_seq[32] == 0  # Thắng và active
    assert status_by_seq[33] == 1  # Thua mẫu 32 nên bị supersede ngay khi ingest

def test_instantaneous_co2_calculations(db_conn):
    config = {"config_id": "cfg_test"}
    sample = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 40,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:10.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:10.500Z",
        "co2_pct": 10.0,
        "flue_flow_m3h": 1000.0,
        "flue_temp_c": 100.0,
        "flue_pressure_gauge_kpa": 5.0,
        "raw_payload_text": "sample_40"
    }
    ingest_raw_sample(db_conn, sample, config)

    cur = db_conn.cursor()
    cur.execute("""
        SELECT flue_pressure_abs_kpa, instantaneous_co2_flow_m3h, instantaneous_co2_std_volume_m3
        FROM raw_samples WHERE sample_seq = 40
    """)
    r = cur.fetchone()
    expected_p_abs = round(5.0 + 101.325, 3)
    assert r["flue_pressure_abs_kpa"] == expected_p_abs

    expected_flow = 1000.0 * 0.10  # 100.0
    assert pytest.approx(r["instantaneous_co2_flow_m3h"], rel=1e-5) == expected_flow

    # std factor = (273.15 / (100 + 273.15)) * (106.325 / 101.325)
    std_factor = (273.15 / 373.15) * (expected_p_abs / 101.325)
    expected_vol = expected_flow * std_factor * (10.0 / 3600.0)
    assert pytest.approx(r["instantaneous_co2_std_volume_m3"], rel=1e-5) == expected_vol

def test_lateness_levels(db_conn):
    config = {"config_id": "cfg_test"}
    # Window: 10:00:00 -> 10:01:00
    # 1. On time: arrived 10:00:50 <= 10:01:00 -> is_late = 0
    s_ontime = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 51,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:10.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:50.000Z", "raw_payload_text": "p51"
    }
    ingest_raw_sample(db_conn, s_ontime, config)

    # 2. Grace period: arrived 10:01:30 <= 10:02:00 -> is_late = 1, is_superseded = 0
    s_grace = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 52,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:20.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:01:30.000Z", "raw_payload_text": "p52"
    }
    ingest_raw_sample(db_conn, s_grace, config)

    # 3. Late: arrived 10:02:05 > 10:02:00 -> is_late = 2, is_superseded = 1
    s_late = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 53,
        "stack_id": "stack_01", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T10:00:30.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:02:05.000Z", "raw_payload_text": "p53"
    }
    ingest_raw_sample(db_conn, s_late, config)

    cur = db_conn.cursor()
    cur.execute("SELECT sample_seq, is_late, is_superseded FROM raw_samples WHERE sample_seq IN (51, 52, 53) ORDER BY sample_seq")
    rows = {r["sample_seq"]: (r["is_late"], r["is_superseded"]) for r in cur.fetchall()}

    assert rows[51] == (0, 0)
    assert rows[52] == (1, 0)
    assert rows[53] == (2, 1)
