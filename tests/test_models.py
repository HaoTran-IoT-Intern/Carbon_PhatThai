import os
import sqlite3
import json
import pytest
from gateway.models import init_db, get_db_connection, load_config, db_transaction

def test_load_config(tmp_path):
    config_data = {
        "config_id": "cfg_cement_stack_v1",
        "version": "1.2.3",
        "gateway_id": "gw_ha_tien_01",
        "api_endpoint": "http://127.0.0.1:5001/api/v1/cems/telemetry/batch",
        "api_key": "cems_gw_secret_key_prod_xxxxxxxxx",
        "standard_conditions": {
            "temperature_k": 273.15,
            "pressure_kpa": 101.325,
            "moisture_handling": "unmodified_same_basis_wet"
        },
        "flue_gas_sensors": {
            "flow_is_pre_standardized": False,
            "pressure_type": "gauge_kpa",
            "pressure_gauge_valid_range_kpa": [-50.0, 50.0],
            "barometric_pressure_kpa_default": 101.325,
            "pressure_standardization_source": "assumed_barometric_101.325kpa"
        },
        "slot_assignment": {
            "slot_duration_seconds": 10,
            "slots_per_minute": 6
        },
        "operational_stacks": [
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": "2026-08-01T00:00:00.000Z"}
        ]
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(config_data))
    
    loaded = load_config(str(cfg_file))
    assert loaded["config_id"] == "cfg_cement_stack_v1"
    assert loaded["version"] == "1.2.3"
    assert loaded["operational_stacks"][0]["stack_id"] == "stack_01"

def test_load_default_config_file():
    loaded = load_config("gateway/config.json")
    assert loaded["config_id"] == "cfg_cement_stack_v1"
    assert loaded["version"] == "1.2.3"
    assert loaded["gateway_id"] == "gw_ha_tien_01"
    assert len(loaded["operational_stacks"]) >= 1


def test_init_db_creates_all_tables(tmp_path):
    db_path = str(tmp_path / "test_cems.db")
    init_db(db_path)
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    
    expected_tables = {
        "raw_samples", 
        "raw_ingest_conflicts", 
        "minute_readings", 
        "batches", 
        "gateway_system_state"
    }
    assert expected_tables.issubset(tables)
    
    # Kiểm tra các cột cốt lõi của schema v1.2.3
    cursor.execute("PRAGMA table_info(raw_samples)")
    raw_cols = {row[1] for row in cursor.fetchall()}
    assert {"flue_pressure_gauge_kpa", "flue_pressure_abs_kpa", "is_superseded", "is_late"}.issubset(raw_cols)

    cursor.execute("PRAGMA table_info(minute_readings)")
    minute_cols = {row[1] for row in cursor.fetchall()}
    assert {"gap_reason", "has_unreconciled_late_data"}.issubset(minute_cols)
    conn.close()

def test_get_db_connection_pragmas(tmp_path):
    db_path = str(tmp_path / "test_pragmas.db")
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"
    cursor.execute("PRAGMA synchronous")
    sync = cursor.fetchone()[0]
    assert sync in (1, "NORMAL", "normal")
    conn.close()

def test_db_transaction_commit_and_rollback(tmp_path):
    db_path = str(tmp_path / "test_tx.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    
    with db_transaction(conn):
        conn.execute("INSERT INTO gateway_system_state (key, value, updated_at) VALUES (?, ?, ?)", ("k1", "v1", "ts1"))
    
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key='k1'")
    assert cur.fetchone()[0] == "v1"
    
    with pytest.raises(RuntimeError):
        with db_transaction(conn):
            conn.execute("INSERT INTO gateway_system_state (key, value, updated_at) VALUES (?, ?, ?)", ("k2", "v2", "ts2"))
            raise RuntimeError("force rollback")
            
    cur.execute("SELECT value FROM gateway_system_state WHERE key='k2'")
    assert cur.fetchone() is None
    conn.close()
