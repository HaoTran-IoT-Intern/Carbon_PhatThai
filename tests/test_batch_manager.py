# tests/test_batch_manager.py
import hashlib
import json
from datetime import datetime, timezone, timedelta
import pytest
from gateway.models import init_db, get_db_connection
from gateway.aggregator import startup_gap_backfill, recompute_minute_rollup
from gateway.slot_manager import ingest_raw_sample
from gateway.batch_manager import freeze_and_create_batch, build_canonical_json_payload

def test_freeze_and_create_batch_immutability(tmp_path):
    db_path = str(tmp_path / "test_cems.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    now = datetime.now(timezone.utc)
    config = {
        "config_id": "cfg_test",
        "operational_stacks": [
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:00.000Z")}
        ]
    }
    startup_gap_backfill(conn, config)
    
    batch_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)
    assert batch_id is not None
    
    cur = conn.cursor()
    cur.execute("SELECT status, readings_count, payload_json FROM batches WHERE batch_id = ?", (batch_id,))
    b_row = dict(cur.fetchone())
    assert b_row["status"] == "pending"
    assert b_row["readings_count"] == 5
    
    payload = json.loads(b_row["payload_json"])
    assert payload["schema_version"] == "1.2.3"
    assert payload["batch_id"] == batch_id
    assert len(payload["readings"]) == 5
    
    # Kiểm tra tính đồng nhất của SHA-256
    hash1 = hashlib.sha256(b_row["payload_json"].encode("utf-8")).hexdigest()
    hash2 = hashlib.sha256(b_row["payload_json"].encode("utf-8")).hexdigest()
    assert hash1 == hash2
    conn.close()

def test_freeze_and_create_batch_no_eligible_drafts(tmp_path):
    db_path = str(tmp_path / "test_cems_empty.db")
    init_db(db_path)
    conn = get_db_connection(db_path)

    # 1. Cơ sở dữ liệu rỗng: Không có draft nào
    batch_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)
    assert batch_id is None

    # 2. Có draft nhưng nằm trong grace period (now - 30s > now - 120s cutoff)
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO minute_readings (
            stack_id, device_id, measurement_config_id, minute_ts, window_seconds,
            expected_samples, received_samples, valid_samples, covered_seconds,
            coverage_status, valid_by_metric_json, excluded_by_reason_json,
            minute_qaqc_status, qaqc_checks_json, status, created_at
        ) VALUES ('stack_01', 'node_01', 'cfg_test', ?, 60, 6, 6, 6, 60, 'full', '{}', '{}', 'in_control', '{}', 'draft', ?)
    """, (recent_ts, now.isoformat()))
    conn.commit()

    batch_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)
    assert batch_id is None
    conn.close()

def test_freeze_and_create_batch_limit_and_multiple_batches(tmp_path):
    db_path = str(tmp_path / "test_cems_multi.db")
    init_db(db_path)
    conn = get_db_connection(db_path)

    now = datetime.now(timezone.utc)
    # Tạo 7 phút đủ điều kiện đóng băng (từ 15 phút trước đến 9 phút trước)
    cur = conn.cursor()
    for i in range(15, 8, -1):
        m_ts = (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:00.000Z")
        cur.execute("""
            INSERT INTO minute_readings (
                stack_id, device_id, measurement_config_id, minute_ts, window_seconds,
                expected_samples, received_samples, valid_samples, covered_seconds,
                coverage_status, valid_by_metric_json, excluded_by_reason_json,
                minute_qaqc_status, qaqc_checks_json, status, created_at
            ) VALUES ('stack_01', 'node_01', 'cfg_test', ?, 60, 6, 6, 6, 60, 'full', '{}', '{}', 'in_control', '{}', 'draft', ?)
        """, (m_ts, now.isoformat()))
    conn.commit()

    # Lần đóng băng 1: limit=5 -> lấy 5 phút đầu
    batch1_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)
    assert batch1_id is not None
    cur.execute("SELECT readings_count, status FROM batches WHERE batch_id = ?", (batch1_id,))
    b1 = dict(cur.fetchone())
    assert b1["readings_count"] == 5
    assert b1["status"] == "pending"

    # Lần đóng băng 2: limit=5 -> lấy 2 phút còn lại
    batch2_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)
    assert batch2_id is not None
    assert batch2_id != batch1_id
    cur.execute("SELECT readings_count, status FROM batches WHERE batch_id = ?", (batch2_id,))
    b2 = dict(cur.fetchone())
    assert b2["readings_count"] == 2
    assert b2["status"] == "pending"

    # Lần đóng băng 3: không còn phút nào -> None
    batch3_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)
    assert batch3_id is None

    # Kiểm tra tất cả 7 bản ghi đều chuyển sang status 'batched'
    cur.execute("SELECT count(*) as cnt FROM minute_readings WHERE status = 'batched'")
    assert cur.fetchone()["cnt"] == 7
    conn.close()

def test_freeze_and_create_batch_stack_isolation(tmp_path):
    db_path = str(tmp_path / "test_cems_iso.db")
    init_db(db_path)
    conn = get_db_connection(db_path)

    now = datetime.now(timezone.utc)
    past_ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:00.000Z")

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO minute_readings (
            stack_id, device_id, measurement_config_id, minute_ts, window_seconds,
            expected_samples, received_samples, valid_samples, covered_seconds,
            coverage_status, valid_by_metric_json, excluded_by_reason_json,
            minute_qaqc_status, qaqc_checks_json, status, created_at
        ) VALUES 
        ('stack_01', 'node_01', 'cfg_test', ?, 60, 6, 6, 6, 60, 'full', '{}', '{}', 'in_control', '{}', 'draft', ?),
        ('stack_02', 'node_02', 'cfg_test', ?, 60, 6, 6, 6, 60, 'full', '{}', '{}', 'in_control', '{}', 'draft', ?)
    """, (past_ts, now.isoformat(), past_ts, now.isoformat()))
    conn.commit()

    # Đóng băng cho stack_01
    batch_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)
    assert batch_id is not None

    cur.execute("SELECT stack_id, status FROM minute_readings WHERE stack_id = 'stack_01'")
    assert cur.fetchone()["status"] == "batched"

    cur.execute("SELECT stack_id, status FROM minute_readings WHERE stack_id = 'stack_02'")
    assert cur.fetchone()["status"] == "draft"

    conn.close()

def test_build_canonical_json_payload_format():
    rows = [
        {
            "id": 1,
            "stack_id": "stack_01",
            "device_id": "node_01",
            "measurement_config_id": "cfg_test",
            "minute_ts": "2026-09-18T10:00:00.000Z",
            "window_seconds": 60,
            "gap_reason": None,
            "expected_samples": 6,
            "received_samples": 6,
            "valid_by_metric_json": json.dumps({"co2_pct": 6}),
            "excluded_by_reason_json": json.dumps({"sensor_fault_samples": 0}),
            "gases_and_dust_json": json.dumps({"co2_pct": {"avg": 12.5, "min": 12.0, "max": 13.0, "data_source": "measured"}}),
            "flue_gas_json": json.dumps({"temp_c": {"avg": 120.0, "min": 119.0, "max": 121.0, "data_source": "measured"}}),
            "ambient_json": json.dumps({"temp_c": {"avg": 30.0, "min": 30.0, "max": 30.0, "data_source": "measured"}}),
            "integrated_emissions_json": json.dumps({
                "method": "fixed_10s_rectangular_v1", "valid_samples": 6, "covered_seconds": 60,
                "coverage_status": "full", "co2_flow_actual_m3h": {"avg": 250.0, "min": 240.0, "max": 260.0},
                "co2_standard_volume_observed_m3": 4.12
            }),
            "minute_qaqc_status": "in_control",
            "qaqc_checks_json": json.dumps({"zero_drift_pct": 0.85, "span_drift_pct": 1.20, "is_calibrating": False})
        }
    ]

    payload = build_canonical_json_payload("batch_uuid_123", "gw_test", "2026-09-18T10:05:00.000Z", rows)
    assert payload["schema_version"] == "1.2.3"
    assert payload["batch_id"] == "batch_uuid_123"
    assert payload["gateway_id"] == "gw_test"
    assert payload["batch_created_at"] == "2026-09-18T10:05:00.000Z"
    assert len(payload["readings"]) == 1

    r = payload["readings"][0]
    assert r["stack_id"] == "stack_01"
    assert r["device_id"] == "node_01"
    assert r["measurement_config_id"] == "cfg_test"
    assert r["ts"] == "2026-09-18T10:00:00.000Z"
    assert r["window_seconds"] == 60
    assert r["gap_reason"] is None
    assert r["samples"]["expected"] == 6
    assert r["samples"]["received"] == 6
    assert r["samples"]["valid_by_metric"]["co2_pct"] == 6
    assert r["samples"]["excluded_by_reason"]["sensor_fault_samples"] == 0
    assert r["gases_and_dust"]["co2_pct"]["avg"] == 12.5
    assert r["flue_gas"]["temp_c"]["avg"] == 120.0
    assert r["ambient"]["temp_c"]["avg"] == 30.0
    assert r["integrated_emissions"]["method"] == "fixed_10s_rectangular_v1"
    assert r["integrated_emissions"]["co2_standard_volume_observed_m3"] == 4.12
    assert r["qaqc"]["minute_status"] == "in_control"
    assert r["qaqc"]["zero_drift_pct"] == 0.85
    assert r["qaqc"]["span_drift_pct"] == 1.20
    assert r["qaqc"]["is_calibrating"] is False

    # Canonical serialization check
    dump1 = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    dump2 = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    assert dump1 == dump2
    assert ' ' not in dump1

def test_freeze_and_create_batch_atomic_rollback(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_cems_rollback.db")
    init_db(db_path)
    conn = get_db_connection(db_path)

    now = datetime.now(timezone.utc)
    past_ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:00.000Z")

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO minute_readings (
            stack_id, device_id, measurement_config_id, minute_ts, window_seconds,
            expected_samples, received_samples, valid_samples, covered_seconds,
            coverage_status, valid_by_metric_json, excluded_by_reason_json,
            minute_qaqc_status, qaqc_checks_json, status, created_at
        ) VALUES ('stack_01', 'node_01', 'cfg_test', ?, 60, 6, 6, 6, 60, 'full', '{}', '{}', 'in_control', '{}', 'draft', ?)
    """, (past_ts, now.isoformat()))
    conn.commit()

    import gateway.batch_manager as bm
    def faulty_builder(*args, **kwargs):
        raise RuntimeError("Simulated failure during payload creation")
    monkeypatch.setattr(bm, "build_canonical_json_payload", faulty_builder)

    with pytest.raises(RuntimeError, match="Simulated failure"):
        freeze_and_create_batch(conn, "stack_01", "gw_01", limit=5)

    # Xác nhận transaction đã rollback: Không có batch nào, minute_readings vẫn là draft
    cur.execute("SELECT count(*) as cnt FROM batches")
    assert cur.fetchone()["cnt"] == 0

    cur.execute("SELECT status FROM minute_readings WHERE stack_id = 'stack_01'")
    assert cur.fetchone()["status"] == "draft"

    conn.close()
