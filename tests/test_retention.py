from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from gateway.models import init_db, get_db_connection
from gateway.retention import purge_synced_data, check_disk_watermark, evaluate_emergency_freeze


def test_purge_only_synced_and_older_than_retention(tmp_path):
    db_path = str(tmp_path / "test_cems.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    cur = conn.cursor()
    old_time = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    
    # 1. Mẫu cũ và đã synced, không có cờ đối soát -> Phải bị xóa
    cur.execute("""
        INSERT INTO minute_readings (stack_id, device_id, measurement_config_id, minute_ts, coverage_status, valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json, status, created_at, has_unreconciled_late_data)
        VALUES ('stack_01', 'n1', 'cfg', ?, 'full', '{}', '{}', 'in_control', '{}', 'synced', ?, 0)
    """, (old_time, old_time))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 1, 'stack_01', 'cfg', ?, 'ntp_synced', ?, ?, 0, '{}', 'in_control', 'payload', ?)
    """, (old_time, old_time, old_time, old_time))
    
    # 2. Mẫu cũ nhưng có cờ đối soát (has_unreconciled_late_data = 1) -> TUYỆT ĐỐI KHÔNG XÓA
    cur.execute("""
        INSERT INTO minute_readings (stack_id, device_id, measurement_config_id, minute_ts, coverage_status, valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json, status, created_at, has_unreconciled_late_data)
        VALUES ('stack_01', 'n1', 'cfg', '2026-08-01T00:01:00.000Z', 'full', '{}', '{}', 'in_control', '{}', 'synced', ?, 1)
    """, (old_time,))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 2, 'stack_01', 'cfg', '2026-08-01T00:01:00.000Z', 'ntp_synced', ?, '2026-08-01T00:01:00.000Z', 0, '{}', 'in_control', 'payload', ?)
    """, (old_time, old_time))
    conn.commit()
    
    deleted = purge_synced_data(conn, retention_days=30)
    assert deleted == 1
    
    cur.execute("SELECT count(*) FROM raw_samples")
    assert cur.fetchone()[0] == 1
    conn.close()


def test_purge_does_not_purge_unsynced_quarantined_or_recent(tmp_path):
    db_path = str(tmp_path / "test_retention_edge.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    
    now = datetime.now(timezone.utc)
    old_time_40d = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    recent_time_5d = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    
    # 1. Old (40d) but draft status -> DO NOT PURGE
    cur.execute("""
        INSERT INTO minute_readings (stack_id, device_id, measurement_config_id, minute_ts, coverage_status, valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json, status, created_at, has_unreconciled_late_data)
        VALUES ('stack_01', 'n1', 'cfg', ?, 'full', '{}', '{}', 'in_control', '{}', 'draft', ?, 0)
    """, (old_time_40d, old_time_40d))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 10, 'stack_01', 'cfg', ?, 'ntp_synced', ?, ?, 0, '{}', 'in_control', 'payload', ?)
    """, (old_time_40d, old_time_40d, old_time_40d, old_time_40d))
    
    # 2. Old (40d) but quarantined status -> DO NOT PURGE
    old_time_quarantine = (now - timedelta(days=41)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    cur.execute("""
        INSERT INTO minute_readings (stack_id, device_id, measurement_config_id, minute_ts, coverage_status, valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json, status, created_at, has_unreconciled_late_data)
        VALUES ('stack_01', 'n1', 'cfg', ?, 'full', '{}', '{}', 'in_control', '{}', 'quarantined', ?, 0)
    """, (old_time_quarantine, old_time_quarantine))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 11, 'stack_01', 'cfg', ?, 'ntp_synced', ?, ?, 0, '{}', 'in_control', 'payload', ?)
    """, (old_time_quarantine, old_time_quarantine, old_time_quarantine, old_time_quarantine))

    # 3. Old (40d) but frozen status -> DO NOT PURGE
    old_time_frozen = (now - timedelta(days=42)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    cur.execute("""
        INSERT INTO minute_readings (stack_id, device_id, measurement_config_id, minute_ts, coverage_status, valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json, status, created_at, has_unreconciled_late_data)
        VALUES ('stack_01', 'n1', 'cfg', ?, 'full', '{}', '{}', 'in_control', '{}', 'frozen', ?, 0)
    """, (old_time_frozen, old_time_frozen))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 12, 'stack_01', 'cfg', ?, 'ntp_synced', ?, ?, 0, '{}', 'in_control', 'payload', ?)
    """, (old_time_frozen, old_time_frozen, old_time_frozen, old_time_frozen))

    # 4. Recent (5d) and synced -> DO NOT PURGE
    cur.execute("""
        INSERT INTO minute_readings (stack_id, device_id, measurement_config_id, minute_ts, coverage_status, valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json, status, created_at, has_unreconciled_late_data)
        VALUES ('stack_01', 'n1', 'cfg', ?, 'full', '{}', '{}', 'in_control', '{}', 'synced', ?, 0)
    """, (recent_time_5d, recent_time_5d))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 13, 'stack_01', 'cfg', ?, 'ntp_synced', ?, ?, 0, '{}', 'in_control', 'payload', ?)
    """, (recent_time_5d, recent_time_5d, recent_time_5d, recent_time_5d))

    # 5. Old (45d), synced, has_unreconciled_late_data = 0 -> PURGE! (2 samples in this minute)
    old_time_purge = (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    cur.execute("""
        INSERT INTO minute_readings (stack_id, device_id, measurement_config_id, minute_ts, coverage_status, valid_by_metric_json, excluded_by_reason_json, minute_qaqc_status, qaqc_checks_json, status, created_at, has_unreconciled_late_data)
        VALUES ('stack_01', 'n1', 'cfg', ?, 'full', '{}', '{}', 'in_control', '{}', 'synced', ?, 0)
    """, (old_time_purge, old_time_purge))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 14, 'stack_01', 'cfg', ?, 'ntp_synced', ?, ?, 0, '{}', 'in_control', 'payload', ?)
    """, (old_time_purge, old_time_purge, old_time_purge, old_time_purge))
    cur.execute("""
        INSERT INTO raw_samples (device_id, boot_id, sample_seq, stack_id, measurement_config_id, sample_ts, time_quality, arrived_at, minute_window_ts, slot_index, metric_quality_json, qaqc_status, raw_payload_text, created_at)
        VALUES ('n1', 'b1', 15, 'stack_01', 'cfg', ?, 'ntp_synced', ?, ?, 1, '{}', 'in_control', 'payload', ?)
    """, (old_time_purge, old_time_purge, old_time_purge, old_time_purge))
    conn.commit()

    deleted = purge_synced_data(conn, retention_days=30)
    assert deleted == 2

    # Verify that the 4 unpurged samples remain
    cur.execute("SELECT count(*) FROM raw_samples")
    assert cur.fetchone()[0] == 4

    # Verify that minute_readings rows themselves were NOT deleted
    cur.execute("SELECT count(*) FROM minute_readings")
    assert cur.fetchone()[0] == 5
    conn.close()


def test_purge_synced_data_empty_db(tmp_path):
    db_path = str(tmp_path / "empty.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    deleted = purge_synced_data(conn, retention_days=30)
    assert deleted == 0
    conn.close()


def test_emergency_freeze_hysteresis_logic():
    # Khi bình thường: giảm xuống 4.8% -> Kích hoạt FREEZE
    assert evaluate_emergency_freeze(4.8, "NORMAL") == "FREEZE"
    assert evaluate_emergency_freeze(0.0, "NORMAL") == "FREEZE"
    assert evaluate_emergency_freeze(4.99, "NORMAL") == "FREEZE"
    assert evaluate_emergency_freeze(5.0, "NORMAL") == "NORMAL"
    assert evaluate_emergency_freeze(10.0, "NORMAL") == "NORMAL"
    
    # Khi đang FREEZE: tăng lên 6.5% -> VẪN GIỮ NGUYÊN FREEZE (chưa đạt ngưỡng 8%)
    assert evaluate_emergency_freeze(6.5, "FREEZE") == "FREEZE"
    assert evaluate_emergency_freeze(7.99, "FREEZE") == "FREEZE"
    assert evaluate_emergency_freeze(8.0, "FREEZE") == "FREEZE"
    
    # Khi đang FREEZE: tăng lên 8.2% -> PHỤC HỒI NORMAL
    assert evaluate_emergency_freeze(8.2, "FREEZE") == "NORMAL"
    assert evaluate_emergency_freeze(15.0, "FREEZE") == "NORMAL"


def test_check_disk_watermark():
    # 1. Free is healthy (>10% and >1GB) -> OK
    with patch("shutil.disk_usage", return_value=(100 * 1024**3, 80 * 1024**3, 20 * 1024**3)):
        assert check_disk_watermark(".") == "OK"

    # 2. Free is 8% (between 5% and 10%) but > 1GB -> WARNING
    with patch("shutil.disk_usage", return_value=(100 * 1024**3, 92 * 1024**3, 8 * 1024**3)):
        assert check_disk_watermark(".") == "WARNING"

    # 3. Free pct is 12% (>10%) but absolute free is 900MB (<1GB) -> WARNING
    with patch("shutil.disk_usage", return_value=(7 * 1024**3, 6 * 1024**3, 900 * 1024 * 1024)):
        assert check_disk_watermark(".") == "WARNING"

    # 4. Free is 4% (<5%) -> CRITICAL
    with patch("shutil.disk_usage", return_value=(100 * 1024**3, 96 * 1024**3, 4 * 1024**3)):
        assert check_disk_watermark(".") == "CRITICAL"

    # 5. Free pct is 6% (>5%) but absolute free is 150MB (<200MB) -> CRITICAL
    with patch("shutil.disk_usage", return_value=(2 * 1024**3, 1850 * 1024 * 1024, 150 * 1024 * 1024)):
        assert check_disk_watermark(".") == "CRITICAL"
