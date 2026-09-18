# tests/test_aggregator.py
import pytest
import json
from gateway.models import init_db, get_db_connection
from gateway.slot_manager import ingest_raw_sample
from gateway.aggregator import recompute_minute_rollup

@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test_cems.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    yield conn
    conn.close()

def test_recompute_minute_rollup_full_slots(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T10:00:00.000Z"
    
    # Nạp đủ 6 mẫu cho 6 slot 0..5 với đơn vị áp suất gauge kPa
    for sec in range(0, 60, 10):
        sample = {
            "device_id": "dev_01",
            "boot_id": "b1",
            "sample_seq": sec // 10,
            "stack_id": "stack_01",
            "measurement_config_id": "cfg_test",
            "sample_ts": f"2026-09-18T10:00:{sec:02d}.000Z",
            "time_quality": "ntp_synced",
            "co2_pct": 15.0,
            "flue_temp_c": 150.0,
            "flue_pressure_gauge_kpa": 1.2,
            "flue_flow_m3h": 2000.0,
            "so2_ppm": 85.0,
            "qaqc_status": "in_control",
            "raw_payload_text": f"payload_{sec}"
        }
        ingest_raw_sample(db_conn, sample, config)
        
    recompute_minute_rollup(db_conn, "stack_01", minute_ts, config)
    
    cur = db_conn.cursor()
    cur.execute("SELECT * FROM minute_readings WHERE stack_id = 'stack_01' AND minute_ts = ?", (minute_ts,))
    row = dict(cur.fetchone())
    
    assert row["received_samples"] == 6
    assert row["covered_seconds"] == 60
    assert row["coverage_status"] == "full"
    assert row["minute_qaqc_status"] == "in_control"
    
    emissions = json.loads(row["integrated_emissions_json"])
    assert emissions["valid_samples"] == 6
    assert emissions["covered_seconds"] == 60
    assert emissions["co2_flow_actual_m3h"]["avg"] == pytest.approx(300.0)
    assert emissions["co2_standard_volume_observed_m3"] > 0
    
    flue = json.loads(row["flue_gas_json"])
    assert flue["pressure_gauge_kpa"]["avg"] == pytest.approx(1.2)
    assert flue["pressure_abs_kpa"]["avg"] == pytest.approx(102.525, abs=0.01)

def test_recompute_minute_rollup_partial_and_none_coverage(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T10:00:00.000Z"

    # Trường hợp không có mẫu nào
    recompute_minute_rollup(db_conn, "stack_01", minute_ts, config)
    cur = db_conn.cursor()
    cur.execute("SELECT * FROM minute_readings WHERE stack_id = 'stack_01' AND minute_ts = ?", (minute_ts,))
    row = dict(cur.fetchone())
    assert row["received_samples"] == 0
    assert row["covered_seconds"] == 0
    assert row["coverage_status"] == "none"
    emissions = json.loads(row["integrated_emissions_json"])
    assert emissions["valid_samples"] == 0
    assert emissions["covered_seconds"] == 0
    assert emissions["co2_standard_volume_observed_m3"] is None

    # Nạp 3 mẫu (slot 0, 1, 2)
    for sec in (0, 10, 20):
        sample = {
            "device_id": "dev_01",
            "boot_id": "b1",
            "sample_seq": 100 + (sec // 10),
            "stack_id": "stack_01",
            "measurement_config_id": "cfg_test",
            "sample_ts": f"2026-09-18T10:00:{sec:02d}.000Z",
            "time_quality": "ntp_synced",
            "co2_pct": 10.0,
            "flue_temp_c": 120.0,
            "flue_pressure_gauge_kpa": 0.5,
            "flue_flow_m3h": 1000.0,
            "qaqc_status": "in_control",
            "raw_payload_text": f"payload_part_{sec}"
        }
        ingest_raw_sample(db_conn, sample, config)

    recompute_minute_rollup(db_conn, "stack_01", minute_ts, config)
    cur.execute("SELECT * FROM minute_readings WHERE stack_id = 'stack_01' AND minute_ts = ?", (minute_ts,))
    row = dict(cur.fetchone())
    assert row["received_samples"] == 3
    assert row["valid_samples"] == 3
    assert row["covered_seconds"] == 30
    assert row["coverage_status"] == "partial"
    emissions = json.loads(row["integrated_emissions_json"])
    assert emissions["valid_samples"] == 3
    assert emissions["covered_seconds"] == 30
    assert emissions["co2_flow_actual_m3h"]["avg"] == pytest.approx(100.0)

def test_qaqc_precedence_independent_of_coverage(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T11:00:00.000Z"

    # 1. Toàn bộ out_of_control
    for sec in (0, 10):
        sample = {
            "device_id": "dev_01", "boot_id": "b1", "sample_seq": 200 + (sec // 10),
            "stack_id": "stack_02", "measurement_config_id": "cfg_test",
            "sample_ts": f"2026-09-18T11:00:{sec:02d}.000Z",
            "time_quality": "ntp_synced", "co2_pct": 12.0, "flue_temp_c": 100.0,
            "flue_pressure_gauge_kpa": 0.0, "flue_flow_m3h": 500.0,
            "qaqc_status": "out_of_control", "raw_payload_text": f"payload_ooc_{sec}"
        }
        ingest_raw_sample(db_conn, sample, config)
    recompute_minute_rollup(db_conn, "stack_02", minute_ts, config)
    cur = db_conn.cursor()
    cur.execute("SELECT minute_qaqc_status FROM minute_readings WHERE stack_id = 'stack_02' AND minute_ts = ?", (minute_ts,))
    assert cur.fetchone()["minute_qaqc_status"] == "out_of_control"

    # 2. Hỗn hợp (mixed)
    sample_mixed = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 202,
        "stack_id": "stack_02", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T11:00:20.000Z",
        "time_quality": "ntp_synced", "co2_pct": 12.0, "flue_temp_c": 100.0,
        "flue_pressure_gauge_kpa": 0.0, "flue_flow_m3h": 500.0,
        "qaqc_status": "in_control", "raw_payload_text": "payload_in_ctrl"
    }
    ingest_raw_sample(db_conn, sample_mixed, config)
    recompute_minute_rollup(db_conn, "stack_02", minute_ts, config)
    cur.execute("SELECT minute_qaqc_status FROM minute_readings WHERE stack_id = 'stack_02' AND minute_ts = ?", (minute_ts,))
    assert cur.fetchone()["minute_qaqc_status"] == "mixed"

def test_superseded_samples_ignored(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T12:00:00.000Z"

    # Mẫu 1: slot 0, ntp_synced, co2 = 10.0
    sample1 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 301,
        "stack_id": "stack_03", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T12:00:02.000Z", "time_quality": "gateway_assigned",
        "arrived_at": "2026-09-18T12:00:02.100Z",
        "co2_pct": 10.0, "flue_temp_c": 100.0, "flue_pressure_gauge_kpa": 0.0, "flue_flow_m3h": 500.0,
        "qaqc_status": "in_control", "raw_payload_text": "p301"
    }
    # Mẫu 2: cùng slot 0, time_quality = ntp_synced (thắng mẫu 1), co2 = 20.0
    sample2 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 302,
        "stack_id": "stack_03", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T12:00:05.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T12:00:05.100Z",
        "co2_pct": 20.0, "flue_temp_c": 100.0, "flue_pressure_gauge_kpa": 0.0, "flue_flow_m3h": 500.0,
        "qaqc_status": "in_control", "raw_payload_text": "p302"
    }
    ingest_raw_sample(db_conn, sample1, config)
    ingest_raw_sample(db_conn, sample2, config)

    recompute_minute_rollup(db_conn, "stack_03", minute_ts, config)
    cur = db_conn.cursor()
    cur.execute("SELECT received_samples, gases_and_dust_json FROM minute_readings WHERE stack_id = 'stack_03' AND minute_ts = ?", (minute_ts,))
    row = dict(cur.fetchone())
    assert row["received_samples"] == 1
    gases = json.loads(row["gases_and_dust_json"])
    assert gases["co2_pct"]["avg"] == 20.0

def test_calibrating_sample_excluded_from_co2_valid_samples(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T13:00:00.000Z"

    sample = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 401,
        "stack_id": "stack_04", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T13:00:00.000Z", "time_quality": "ntp_synced",
        "co2_pct": 15.0, "flue_temp_c": 100.0, "flue_pressure_gauge_kpa": 0.0, "flue_flow_m3h": 500.0,
        "qaqc_status": "in_control", "is_calibrating": 1, "raw_payload_text": "p401"
    }
    ingest_raw_sample(db_conn, sample, config)

    recompute_minute_rollup(db_conn, "stack_04", minute_ts, config)
    cur = db_conn.cursor()
    cur.execute("SELECT received_samples, valid_samples, covered_seconds, coverage_status FROM minute_readings WHERE stack_id = 'stack_04' AND minute_ts = ?", (minute_ts,))
    row = dict(cur.fetchone())
    assert row["received_samples"] == 1
    assert row["valid_samples"] == 0
    assert row["covered_seconds"] == 0
    assert row["coverage_status"] == "none"

def test_recompute_minute_rollup_idempotent_update(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T14:00:00.000Z"

    # Lần 1: 1 sample
    s1 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 501,
        "stack_id": "stack_05", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T14:00:00.000Z", "time_quality": "ntp_synced",
        "co2_pct": 10.0, "flue_temp_c": 100.0, "flue_pressure_gauge_kpa": 0.0, "flue_flow_m3h": 500.0,
        "qaqc_status": "in_control", "raw_payload_text": "p501"
    }
    ingest_raw_sample(db_conn, s1, config)
    recompute_minute_rollup(db_conn, "stack_05", minute_ts, config)

    cur = db_conn.cursor()
    cur.execute("SELECT received_samples, valid_samples FROM minute_readings WHERE stack_id = 'stack_05' AND minute_ts = ?", (minute_ts,))
    r1 = dict(cur.fetchone())
    assert r1["received_samples"] == 1
    assert r1["valid_samples"] == 1

    # Lần 2: thêm sample vào slot khác rồi recompute lại
    s2 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 502,
        "stack_id": "stack_05", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T14:00:10.000Z", "time_quality": "ntp_synced",
        "co2_pct": 12.0, "flue_temp_c": 100.0, "flue_pressure_gauge_kpa": 0.0, "flue_flow_m3h": 500.0,
        "qaqc_status": "in_control", "raw_payload_text": "p502"
    }
    ingest_raw_sample(db_conn, s2, config)
    recompute_minute_rollup(db_conn, "stack_05", minute_ts, config)

    cur.execute("SELECT count(*), received_samples, valid_samples, covered_seconds FROM minute_readings WHERE stack_id = 'stack_05' AND minute_ts = ?", (minute_ts,))
    r2 = cur.fetchone()
    assert r2[0] == 1  # Không tạo duplicate row
    assert r2["received_samples"] == 2
    assert r2["valid_samples"] == 2
    assert r2["covered_seconds"] == 20

def test_qaqc_out_of_control_and_sensor_fault_excluded(db_conn):
    config = {"config_id": "cfg_test"}
    minute_ts = "2026-09-18T15:00:00.000Z"

    # Sample 1: Normal valid
    s1 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 601,
        "stack_id": "stack_06", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T15:00:00.000Z", "time_quality": "ntp_synced",
        "co2_pct": 14.0, "so2_ppm": 80.0, "nox_ppm": 120.0, "co_ppm": 35.0, "o2_pct": 18.0, "pm_mgm3": 12.0,
        "flue_temp_c": 150.0, "flue_pressure_gauge_kpa": 1.0, "flue_flow_m3h": 2000.0,
        "ambient_temp_c": 28.0, "ambient_humidity_pct": 65.0,
        "qaqc_status": "in_control", "is_calibrating": 0,
        "metric_quality_json": "{}", "raw_payload_text": "p601"
    }

    # Sample 2: QA/QC out_of_control (must be excluded from co2_valid_samples and count in qaqc_failed_samples)
    s2 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 602,
        "stack_id": "stack_06", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T15:00:10.000Z", "time_quality": "ntp_synced",
        "co2_pct": 16.0, "so2_ppm": 85.0, "nox_ppm": 125.0, "co_ppm": 36.0, "o2_pct": 17.5, "pm_mgm3": 13.0,
        "flue_temp_c": 152.0, "flue_pressure_gauge_kpa": 1.1, "flue_flow_m3h": 2050.0,
        "ambient_temp_c": 28.5, "ambient_humidity_pct": 64.0,
        "qaqc_status": "out_of_control", "is_calibrating": 0,
        "metric_quality_json": "{}", "raw_payload_text": "p602"
    }

    # Sample 3: Calibrating (must be excluded from co2_valid_samples and count in calibrating_samples)
    s3 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 603,
        "stack_id": "stack_06", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T15:00:20.000Z", "time_quality": "ntp_synced",
        "co2_pct": 15.0, "so2_ppm": 82.0, "nox_ppm": 122.0, "co_ppm": 34.0, "o2_pct": 18.2, "pm_mgm3": 11.0,
        "flue_temp_c": 149.0, "flue_pressure_gauge_kpa": 1.0, "flue_flow_m3h": 2000.0,
        "ambient_temp_c": 28.2, "ambient_humidity_pct": 65.0,
        "qaqc_status": "in_control", "is_calibrating": 1,
        "metric_quality_json": "{}", "raw_payload_text": "p603"
    }

    # Sample 4: Sensor fault on SO2 (-999.0 or metric_quality sensor_fault)
    s4 = {
        "device_id": "dev_01", "boot_id": "b1", "sample_seq": 604,
        "stack_id": "stack_06", "measurement_config_id": "cfg_test",
        "sample_ts": "2026-09-18T15:00:30.000Z", "time_quality": "ntp_synced",
        "co2_pct": 14.5, "so2_ppm": -999.0, "nox_ppm": 121.0, "co_ppm": 35.0, "o2_pct": 18.0, "pm_mgm3": 12.0,
        "flue_temp_c": 150.0, "flue_pressure_gauge_kpa": 1.0, "flue_flow_m3h": 2000.0,
        "ambient_temp_c": 28.0, "ambient_humidity_pct": 65.0,
        "qaqc_status": "in_control", "is_calibrating": 0,
        "metric_quality_json": json.dumps({"so2_ppm": "sensor_fault"}), "raw_payload_text": "p604"
    }

    for s in [s1, s2, s3, s4]:
        ingest_raw_sample(db_conn, s, config)

    recompute_minute_rollup(db_conn, "stack_06", minute_ts, config)

    cur = db_conn.cursor()
    cur.execute("SELECT * FROM minute_readings WHERE stack_id = 'stack_06' AND minute_ts = ?", (minute_ts,))
    row = dict(cur.fetchone())

    assert row["received_samples"] == 4
    # Valid for CO2 integration: only s1 and s4 (s2 is out_of_control, s3 is calibrating)
    assert row["valid_samples"] == 2
    assert row["covered_seconds"] == 20

    # Verify SO2 average does not include -999.0
    gases = json.loads(row["gases_and_dust_json"])
    assert gases["so2_ppm"]["min"] > 0
    assert gases["so2_ppm"]["avg"] == pytest.approx((80.0 + 85.0 + 82.0) / 3, abs=0.1)

    # Verify valid_by_metric_json has all 11 metrics
    valid_metrics = json.loads(row["valid_by_metric_json"])
    required_metrics = [
        "co2_pct", "so2_ppm", "nox_ppm", "co_ppm", "o2_pct", "pm_mgm3",
        "flue_temp_c", "flue_pressure_abs_kpa", "flue_flow_m3h", "ambient_temp_c", "ambient_humidity_pct"
    ]
    for m in required_metrics:
        assert m in valid_metrics, f"Missing metric {m} in valid_by_metric"
    assert valid_metrics["so2_ppm"] == 3  # 1 had sensor fault

    # Verify excluded_by_reason_json
    reasons = json.loads(row["excluded_by_reason_json"])
    assert reasons["qaqc_failed_samples"] == 1
    assert reasons["calibrating_samples"] == 1
    assert reasons["sensor_fault_samples"] == 1

