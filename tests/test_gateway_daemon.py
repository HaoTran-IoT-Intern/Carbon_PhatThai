import pytest
import json
from unittest.mock import MagicMock, patch
from gateway.models import init_db, get_db_connection
from gateway.gateway import GatewayDaemon

@pytest.fixture
def daemon_env(tmp_path):
    db_path = str(tmp_path / "daemon_test.db")
    init_db(db_path)
    config = {
        "gateway_id": "gw_daemon_test",
        "database_path": db_path,
        "api_endpoint": "http://127.0.0.1:5001/api/v1/cems/telemetry/batch",
        "api_key": "test_key",
        "operational_stacks": [
            {
                "stack_id": "stack_01",
                "device_id": "node_01",
                "is_active": True,
                "active_from": "2026-09-18T10:00:00.000Z"
            }
        ],
        "flue_gas_sensors": {
            "barometric_pressure_kpa_default": 101.325,
            "pressure_gauge_valid_range_kpa": [-50.0, 50.0]
        }
    }
    daemon = GatewayDaemon(config=config)
    yield daemon
    daemon.shutdown()

def test_gateway_daemon_initialization_and_backfill(daemon_env):
    daemon = daemon_env
    assert daemon.conn is not None
    # Verify DB tables were initialized
    cur = daemon.conn.cursor()
    cur.execute("SELECT count(*) FROM raw_samples")
    assert cur.fetchone()[0] == 0

def test_gateway_daemon_on_mqtt_message(daemon_env):
    daemon = daemon_env
    sample_payload = {
        "device_id": "node_01",
        "boot_id": "b_daemon_1",
        "sample_seq": 1,
        "stack_id": "stack_01",
        "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T10:00:10.000Z",
        "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:10.500Z",
        "co2_pct": 14.5,
        "flue_flow_m3h": 2000.0,
        "flue_temp_c": 150.0,
        "flue_pressure_gauge_kpa": 1.5,
        "ambient_temp_c": 28.0,
        "ambient_humidity_pct": 65.0,
        "qaqc_status": "in_control"
    }
    
    mock_msg = MagicMock()
    mock_msg.topic = "pione/carbon/v1/node_01/data"
    mock_msg.payload = json.dumps(sample_payload).encode("utf-8")
    
    status = daemon.on_mqtt_message(None, None, mock_msg)
    assert status == "accepted"
    
    cur = daemon.conn.cursor()
    cur.execute("SELECT count(*) FROM raw_samples WHERE boot_id = 'b_daemon_1'")
    assert cur.fetchone()[0] == 1

def test_gateway_daemon_run_pipeline_cycle(daemon_env):
    daemon = daemon_env
    # 1. Nạp mẫu thô cho phút 10:00:00Z
    sample_payload = {
        "device_id": "node_01", "boot_id": "b_cycle", "sample_seq": 1,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T10:00:10.000Z", "time_quality": "ntp_synced",
        "arrived_at": "2026-09-18T10:00:10.500Z",
        "co2_pct": 14.5, "flue_flow_m3h": 2000.0, "flue_temp_c": 150.0, "flue_pressure_gauge_kpa": 1.5,
        "ambient_temp_c": 28.0, "ambient_humidity_pct": 65.0, "qaqc_status": "in_control"
    }
    mock_msg = MagicMock()
    mock_msg.topic = "pione/carbon/v1/node_01/data"
    mock_msg.payload = json.dumps(sample_payload).encode("utf-8")
    daemon.on_mqtt_message(None, None, mock_msg)
    
    # 2. Chạy 1 chu kỳ aggregation
    daemon.run_aggregation_cycle(["2026-09-18T10:00:00.000Z"])
    
    cur = daemon.conn.cursor()
    cur.execute("SELECT status, valid_samples FROM minute_readings WHERE minute_ts = '2026-09-18T10:00:00.000Z'")
    row = cur.fetchone()
    assert row is not None
    assert row["status"] == "draft"
    assert row["valid_samples"] == 1
    
    # 3. Đóng băng và dispatch với mock HTTP
    mock_session = MagicMock()
    def mock_post(url, headers=None, data=None, timeout=None):
        body = json.loads(data) if isinstance(data, str) else data
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {
            "status": "success",
            "batch_id": body["batch_id"],
            "processed_count": len(body["readings"]),
            "inserted_count": len(body["readings"]),
            "server_time": "2026-09-18T10:05:00.000Z"
        }
        return resp
    mock_session.post.side_effect = mock_post

    from datetime import datetime, timezone
    mock_freeze_dt = datetime.fromisoformat("2026-09-18T10:05:00.000+00:00")
    dispatch_res = daemon.run_batch_and_dispatch_cycle(mock_session, current_time_dt=mock_freeze_dt)
    
    assert dispatch_res["dispatched_count"] == 1
    
    cur.execute("SELECT status FROM minute_readings WHERE minute_ts = '2026-09-18T10:00:00.000Z'")
    assert cur.fetchone()["status"] == "synced"
