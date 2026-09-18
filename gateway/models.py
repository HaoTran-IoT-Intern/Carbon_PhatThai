# gateway/models.py
import os
import sqlite3
import json
from contextlib import contextmanager

def load_config(config_path="gateway/config.json"):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_db_connection(db_path="gateway/cems_data.db"):
    dir_name = os.path.dirname(os.path.abspath(db_path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

@contextmanager
def db_transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise

def init_db(db_path="gateway/cems_data.db"):
    conn = get_db_connection(db_path)
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            sample_seq INTEGER NOT NULL,
            stack_id TEXT NOT NULL,
            measurement_config_id TEXT NOT NULL,
            sample_ts TEXT,
            time_quality TEXT NOT NULL,
            arrived_at TEXT NOT NULL,
            minute_window_ts TEXT,
            slot_index INTEGER,
            is_superseded INTEGER DEFAULT 0,
            is_late INTEGER DEFAULT 0,
            co2_pct REAL,
            so2_ppm REAL,
            nox_ppm REAL,
            co_ppm REAL,
            o2_pct REAL,
            pm_mgm3 REAL,
            flue_temp_c REAL,
            flue_pressure_gauge_kpa REAL,
            flue_pressure_abs_kpa REAL,
            flue_flow_m3h REAL,
            ambient_temp_c REAL,
            ambient_humidity_pct REAL,
            instantaneous_co2_flow_m3h REAL,
            instantaneous_co2_std_volume_m3 REAL,
            metric_quality_json TEXT NOT NULL,
            qaqc_status TEXT NOT NULL,
            is_calibrating INTEGER DEFAULT 0,
            raw_payload_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(device_id, boot_id, sample_seq)
        );
        CREATE INDEX IF NOT EXISTS idx_raw_slot ON raw_samples(stack_id, minute_window_ts, slot_index, is_superseded);

        CREATE TABLE IF NOT EXISTS raw_ingest_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            sample_seq INTEGER NOT NULL,
            existing_payload TEXT NOT NULL,
            conflicting_payload TEXT NOT NULL,
            detected_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS minute_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stack_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            measurement_config_id TEXT NOT NULL,
            minute_ts TEXT NOT NULL,
            window_seconds INTEGER DEFAULT 60,
            gap_reason TEXT,
            expected_samples INTEGER DEFAULT 6,
            received_samples INTEGER DEFAULT 0,
            valid_samples INTEGER DEFAULT 0,
            covered_seconds INTEGER DEFAULT 0,
            coverage_status TEXT NOT NULL,
            valid_by_metric_json TEXT NOT NULL,
            excluded_by_reason_json TEXT NOT NULL,
            gases_and_dust_json TEXT,
            flue_gas_json TEXT,
            ambient_json TEXT,
            integrated_emissions_json TEXT,
            minute_qaqc_status TEXT NOT NULL,
            qaqc_checks_json TEXT NOT NULL,
            has_unreconciled_late_data INTEGER DEFAULT 0,
            batch_id TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            synced_at TEXT,
            UNIQUE(stack_id, minute_ts)
        );
        CREATE INDEX IF NOT EXISTS idx_minute_status ON minute_readings(status, minute_ts);

        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            stack_id TEXT NOT NULL,
            gateway_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            readings_count INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER DEFAULT 0,
            last_attempt_at TEXT,
            last_http_code INTEGER,
            last_error_message TEXT,
            next_retry_at TEXT,
            synced_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_batch_status ON batches(status, stack_id, next_retry_at);

        CREATE TABLE IF NOT EXISTS gateway_system_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)
    conn.close()
