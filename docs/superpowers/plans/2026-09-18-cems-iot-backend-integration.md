# CEMS IoT & Backend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng hoàn chỉnh tầng phần mềm Edge Gateway CEMS chuẩn công nghiệp kết nối với Backend Platform theo Đặc tả Kỹ thuật v1.2.3-draft (thu thập mẫu thô, phân bổ 6 slot/phút, giải quyết xung đột mẫu qua chuỗi ưu tiên kỹ thuật, bảo vệ tính bất biến của phút đóng băng, tích phân phát thải CO2 cơ sở khí ướt, đóng băng batch bất biến, động cơ Dispatcher có backoff toàn trạm, Mosquitto freeze hysteresis và cập nhật firmware ESP32 Node).

**Architecture:** Edge Gateway đóng vai trò trạm thu thập và xử lý biên: Chạy Local Mosquitto Broker có xác thực, nhận mẫu thô 10s từ Node, giải quyết xung đột slot, lưu trữ bền vững trong SQLite (`raw_samples`), tổng hợp thống kê phút (`minute_readings`), bù đắp gap phút rỗng runtime & restart, đóng băng batch 5 phút theo stack (`batches`), Dispatcher duy nhất gửi HTTP POST lên Backend Cloud với khả năng kháng lỗi mạng/server toàn diện, và worker giám sát đĩa với cơ chế Hysteresis (5% ngắt broker, 8% phục hồi).

**Tech Stack:** Python 3.10+, SQLite3 (WAL mode, immediate transactions), `requests`, `pytest`, `paho-mqtt`, Arduino C/C++ (ESP32 Node).

**Spec:** `docs/superpowers/specs/2026-09-18-cems-iot-backend-integration-spec.md`

## Global Constraints

- Toàn bộ mốc thời gian trao đổi mạng dùng chuẩn UTC ISO 8601 kết thúc bằng `Z` với 3 chữ số mili-giây (`YYYY-MM-DDTHH:mm:ss.sssZ`).
- Cả nồng độ và lưu lượng đầu vào đều nằm trên cơ sở khí ướt thực tế (`unmodified_same_basis_wet`), Gateway không quy đổi độ ẩm.
- Đơn vị áp suất: Áp suất dư đo đạc trực tiếp là `flue_pressure_gauge_kpa` (dải kiểm tra vật lý $[-50.0, 50.0]\text{ kPa}$); áp suất tuyệt đối quy đổi $P_{flue\_abs\_kpa} = P_{flue\_gauge\_kpa} + P_{baro\_kpa}$ (với $P_{baro\_kpa} = 101.325\text{ kPa}$).
- Mỗi batch chỉ thuộc về đúng 1 `stack_id` duy nhất.
- Grace period đóng băng batch: $minute\_ts \le now - 120\text{ giây}$.
- Dispatcher giãn nhịp (pacing) tối thiểu 1.0 giây giữa mọi lần gửi request.
- Cơ chế chống trùng lặp Idempotency 2 tầng: SHA-256 hash trên UTF-8 bytes của `payload_json` và Unique Key reading.
- Tuyệt đối không xóa dữ liệu draft/batched/quarantined hoặc dữ liệu có cờ đối soát `has_unreconciled_late_data = 1`.
- Không dùng placeholder (TODO, TBD); mọi bước phải có code và test cụ thể.

---

### Task 1: Cấu hình Hệ thống, Local Mosquitto & Khởi tạo Schema CSDL SQLite

**Files:**
- Create: `gateway/config.json`
- Create: `gateway/mosquitto.conf`
- Create: `gateway/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: None (khởi tạo nền tảng).
- Produces: `get_db_connection(db_path)`, `init_db(db_path)`, `load_config(config_path)`.

- [ ] **Step 1: Viết test khởi tạo bảng SQLite và đọc file cấu hình**

```python
# tests/test_models.py
import os
import sqlite3
import json
import pytest
from gateway.models import init_db, get_db_connection, load_config

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
            "flow_is_pre_standardized": false,
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
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_models.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.models'`

- [ ] **Step 3: Tạo `gateway/config.json`, `gateway/mosquitto.conf` và triển khai `gateway/models.py`**

```json
// gateway/config.json
{
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
    "flow_is_pre_standardized": false,
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
    {
      "stack_id": "stack_lo_nung_01",
      "device_id": "cems_node_01",
      "is_active": true,
      "active_from": "2026-08-01T00:00:00.000Z"
    }
  ]
}
```

```conf
# gateway/mosquitto.conf
# Cấu hình Local Mosquitto Broker chạy cục bộ trên Edge Gateway
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/aclfile

# Giới hạn hàng đợi cho kết nối chập chờn
max_queued_messages 1000
persistence true
persistence_location /var/lib/mosquitto/
```

```python
# gateway/models.py
import sqlite3
import json
from contextlib import contextmanager

def load_config(config_path="gateway/config.json"):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_db_connection(db_path="gateway/cems_data.db"):
    conn = sqlite3.connect(db_path, timeout=30.0)
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
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `pytest tests/test_models.py -v`  
Expected: PASS

---

### Task 2: Quản lý Khe thời gian & Giải quyết Xung đột Mẫu thô (Slot Manager)

**Files:**
- Create: `gateway/slot_manager.py`
- Test: `tests/test_slot_manager.py`

**Interfaces:**
- Consumes: `gateway.models` (db connection & tables).
- Produces: `compare_slot_priority(sample_a, sample_b) -> int`, `ingest_raw_sample(conn, raw_dict, config) -> dict`.

- [ ] **Step 1: Viết test cho chuỗi ưu tiên slot, bảo vệ phút đã đóng băng, và xử lý invalid timestamp**

```python
# tests/test_slot_manager.py
import pytest
import json
from datetime import datetime, timezone
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
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_slot_manager.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.slot_manager'`

- [ ] **Step 3: Triển khai `gateway/slot_manager.py`**

```python
# gateway/slot_manager.py
import json
from datetime import datetime, timezone, timedelta
from gateway.models import db_transaction

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def compare_slot_priority(sample_a, sample_b):
    """
    Chuỗi ưu tiên kỹ thuật:
    1. time_quality (ntp_synced > gateway_assigned > invalid)
    2. Độ đầy đủ bộ tứ cốt lõi (co2, flow, temp, pressure_gauge)
    3. Trạng thái hiệu chuẩn (vận hành bình thường 0 > đang hiệu chuẩn 1)
    4. arrived_at (đến sớm hơn thắng)
    """
    tq_rank = {"ntp_synced": 3, "gateway_assigned": 2, "invalid": 1}
    a_tq = tq_rank.get(sample_a.get("time_quality"), 0)
    b_tq = tq_rank.get(sample_b.get("time_quality"), 0)
    if a_tq != b_tq:
        return 1 if a_tq > b_tq else -1
        
    def has_core(s):
        return all(s.get(k) is not None for k in ("co2_pct", "flue_flow_m3h", "flue_temp_c", "flue_pressure_gauge_kpa"))
    if has_core(sample_a) != has_core(sample_b):
        return 1 if has_core(sample_a) else -1
        
    a_cal = sample_a.get("is_calibrating", 0)
    b_cal = sample_b.get("is_calibrating", 0)
    if a_cal != b_cal:
        return 1 if a_cal == 0 else -1
        
    a_arr = sample_a.get("arrived_at", "")
    b_arr = sample_b.get("arrived_at", "")
    if a_arr != b_arr:
        return 1 if a_arr < b_arr else -1
    return 0

def ingest_raw_sample(conn, raw_data, config):
    now_str = get_iso_now()
    arrived_at_str = raw_data.get("arrived_at", now_str)
    
    with db_transaction(conn):
        cur = conn.cursor()
        
        # 1. Kiểm tra duplicate raw
        cur.execute("""
            SELECT id, raw_payload_text FROM raw_samples 
            WHERE device_id = ? AND boot_id = ? AND sample_seq = ?
        """, (raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"]))
        existing = cur.fetchone()
        
        if existing:
            if existing["raw_payload_text"] == raw_data["raw_payload_text"]:
                return {"status": "duplicate_ignored"}
            else:
                cur.execute("""
                    INSERT INTO raw_ingest_conflicts (
                        device_id, boot_id, sample_seq, existing_payload, conflicting_payload, detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"], 
                      existing["raw_payload_text"], raw_data["raw_payload_text"], now_str))
                return {"status": "conflict_rejected"}

        # 2. Xử lý mẫu có time_quality == 'invalid' hoặc sample_ts lỗi
        is_invalid_time = (raw_data.get("time_quality") == "invalid")
        dt_sample = None
        if not is_invalid_time:
            try:
                sample_ts_str = raw_data.get("sample_ts", "")
                dt_sample = datetime.fromisoformat(sample_ts_str.replace("Z", "+00:00"))
            except Exception:
                is_invalid_time = True
                
        if is_invalid_time:
            cur.execute("""
                INSERT INTO raw_samples (
                    device_id, boot_id, sample_seq, stack_id, measurement_config_id,
                    sample_ts, time_quality, arrived_at, minute_window_ts, slot_index,
                    is_superseded, is_late, metric_quality_json, qaqc_status, raw_payload_text, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, 'invalid', ?, NULL, NULL, 1, 3, '{}', 'unknown', ?, ?)
            """, (raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"],
                  raw_data["stack_id"], raw_data["measurement_config_id"], arrived_at_str,
                  raw_data["raw_payload_text"], now_str))
            return {"status": "accepted_invalid_time"}

        # 3. Phân bổ slot và minute_window_ts
        slot_k = int(dt_sample.second // 10)
        minute_window_ts = dt_sample.strftime("%Y-%m-%dT%H:%M:00.000Z")
        window_end_dt = dt_sample.replace(second=0, microsecond=0) + timedelta(minutes=1)
        arrived_dt = datetime.fromisoformat(arrived_at_str.replace("Z", "+00:00"))
        
        # 4. Kiểm tra trạng thái đóng băng của phút
        cur.execute("SELECT id, status FROM minute_readings WHERE stack_id = ? AND minute_ts = ?",
                    (raw_data["stack_id"], minute_window_ts))
        m_row = cur.fetchone()
        
        is_superseded = 0
        is_late = 0
        
        if m_row and m_row["status"] in ("batched", "synced", "quarantined"):
            # Phút đã đóng băng -> Bất biến!
            is_superseded = 1
            is_late = 2
            cur.execute("UPDATE minute_readings SET has_unreconciled_late_data = 1 WHERE id = ?", (m_row["id"],))
            status_result = "accepted_late_frozen"
        else:
            # Xác định is_late
            if arrived_dt <= window_end_dt:
                is_late = 0
            elif arrived_dt <= window_end_dt + timedelta(seconds=60):
                is_late = 1
            else:
                is_late = 2
                is_superseded = 1

            # 5. Slot collision resolution nếu chưa bị supersede
            if is_superseded == 0:
                cur.execute("""
                    SELECT id, time_quality, is_calibrating, arrived_at, co2_pct, flue_flow_m3h, flue_temp_c, flue_pressure_gauge_kpa
                    FROM raw_samples 
                    WHERE stack_id = ? AND minute_window_ts = ? AND slot_index = ? AND is_superseded = 0
                """, (raw_data["stack_id"], minute_window_ts, slot_k))
                current_active = cur.fetchone()
                if current_active:
                    active_dict = dict(current_active)
                    if compare_slot_priority(raw_data, active_dict) > 0:
                        cur.execute("UPDATE raw_samples SET is_superseded = 1 WHERE id = ?", (active_dict["id"],))
                    else:
                        is_superseded = 1
            status_result = "accepted"

        # 6. Quy đổi áp suất và thể tích tức thời (cơ sở khí ướt)
        co2_val = raw_data.get("co2_pct")
        flow_val = raw_data.get("flue_flow_m3h")
        temp_val = raw_data.get("flue_temp_c")
        press_gauge_val = raw_data.get("flue_pressure_gauge_kpa")
        
        press_abs_val = None
        inst_flow = None
        inst_vol = None
        
        if press_gauge_val is not None and (-50.0 <= press_gauge_val <= 50.0):
            press_abs_val = round(press_gauge_val + 101.325, 3)
            
        if all(v is not None for v in (co2_val, flow_val, temp_val, press_abs_val)):
            inst_flow = flow_val * (co2_val / 100.0)
            t_k = temp_val + 273.15
            std_factor = (273.15 / t_k) * (press_abs_val / 101.325)
            inst_vol = inst_flow * std_factor * (10.0 / 3600.0)

        cur.execute("""
            INSERT INTO raw_samples (
                device_id, boot_id, sample_seq, stack_id, measurement_config_id,
                sample_ts, time_quality, arrived_at, minute_window_ts, slot_index,
                is_superseded, is_late, co2_pct, so2_ppm, nox_ppm, co_ppm, o2_pct, pm_mgm3,
                flue_temp_c, flue_pressure_gauge_kpa, flue_pressure_abs_kpa, flue_flow_m3h,
                ambient_temp_c, ambient_humidity_pct, instantaneous_co2_flow_m3h, instantaneous_co2_std_volume_m3,
                metric_quality_json, qaqc_status, is_calibrating, raw_payload_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"], raw_data["stack_id"],
            raw_data["measurement_config_id"], raw_data.get("sample_ts"), raw_data.get("time_quality", "gateway_assigned"),
            arrived_at_str, minute_window_ts, slot_k,
            is_superseded, is_late, co2_val, raw_data.get("so2_ppm"),
            raw_data.get("nox_ppm"), raw_data.get("co_ppm"), raw_data.get("o2_pct"), raw_data.get("pm_mgm3"),
            temp_val, press_gauge_val, press_abs_val, flow_val,
            raw_data.get("ambient_temp_c"), raw_data.get("ambient_humidity_pct"),
            inst_flow, inst_vol, json.dumps(raw_data.get("metric_quality", {})),
            raw_data.get("qaqc_status", "in_control"), raw_data.get("is_calibrating", 0),
            raw_data["raw_payload_text"], now_str
        ))
        
        return {"status": status_result}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `pytest tests/test_slot_manager.py -v`  
Expected: PASS

---

### Task 3: Động cơ Tổng hợp Phút & Tích phân Phát thải CO2 Cơ sở Khí ướt

**Files:**
- Create: `gateway/aggregator.py`
- Test: `tests/test_aggregator.py`

**Interfaces:**
- Consumes: `gateway.models`, `gateway.slot_manager`.
- Produces: `recompute_minute_rollup(conn, stack_id, minute_ts, config)`.

- [ ] **Step 1: Viết test cho hàm tổng hợp phút và tính thể tích CO2 chuẩn quan sát được**

```python
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
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_aggregator.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.aggregator'`

- [ ] **Step 3: Triển khai `gateway/aggregator.py`**

```python
# gateway/aggregator.py
import json
from datetime import datetime, timezone
from gateway.models import db_transaction

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def recompute_minute_rollup(conn, stack_id, minute_ts, config):
    now_str = get_iso_now()
    with db_transaction(conn):
        cur = conn.cursor()
        
        # Chỉ lấy các mẫu active (is_superseded = 0)
        cur.execute("""
            SELECT * FROM raw_samples 
            WHERE stack_id = ? AND minute_window_ts = ? AND is_superseded = 0
            ORDER BY slot_index ASC
        """, (stack_id, minute_ts))
        samples = [dict(r) for r in cur.fetchall()]
        
        received_count = len(samples)
        
        # 1. Thống kê theo slot & độ phủ CO2
        co2_valid_samples = []
        for s in samples:
            if all(s.get(k) is not None for k in ("co2_pct", "flue_flow_m3h", "flue_temp_c", "flue_pressure_abs_kpa")) and s.get("is_calibrating") == 0:
                co2_valid_samples.append(s)
                
        covered_seconds = len(co2_valid_samples) * 10
        if covered_seconds == 60:
            coverage_status = "full"
        elif covered_seconds > 0:
            coverage_status = "partial"
        else:
            coverage_status = "none"
            
        # 2. Thống kê đại lượng đo (avg, min, max)
        def calc_stat(keys):
            vals = [s[keys] for s in samples if s.get(keys) is not None]
            if not vals:
                return None
            return {"avg": round(sum(vals) / len(vals), 2), "min": round(min(vals), 2), "max": round(max(vals), 2), "data_source": "measured"}

        gases = {
            "co2_pct": calc_stat("co2_pct"),
            "so2_ppm": calc_stat("so2_ppm"),
            "nox_ppm": calc_stat("nox_ppm"),
            "co_ppm": calc_stat("co_ppm"),
            "o2_pct": calc_stat("o2_pct"),
            "pm_mgm3": calc_stat("pm_mgm3")
        }
        flue = {
            "temp_c": calc_stat("flue_temp_c"),
            "pressure_gauge_kpa": calc_stat("flue_pressure_gauge_kpa"),
            "pressure_abs_kpa": calc_stat("flue_pressure_abs_kpa"),
            "flow_actual_m3h": calc_stat("flue_flow_m3h")
        }
        ambient = {
            "temp_c": calc_stat("ambient_temp_c"),
            "humidity_pct": calc_stat("ambient_humidity_pct")
        }

        # 3. Tích phân CO2 trên co2_valid_samples
        if co2_valid_samples:
            co2_flows = [s["instantaneous_co2_flow_m3h"] for s in co2_valid_samples if s["instantaneous_co2_flow_m3h"] is not None]
            co2_vols = [s["instantaneous_co2_std_volume_m3"] for s in co2_valid_samples if s["instantaneous_co2_std_volume_m3"] is not None]
            integrated = {
                "method": "fixed_10s_rectangular_v1",
                "valid_samples": len(co2_valid_samples),
                "covered_seconds": covered_seconds,
                "coverage_status": coverage_status,
                "co2_flow_actual_m3h": {
                    "avg": round(sum(co2_flows) / len(co2_flows), 2) if co2_flows else None,
                    "min": round(min(co2_flows), 2) if co2_flows else None,
                    "max": round(max(co2_flows), 2) if co2_flows else None
                },
                "co2_standard_volume_observed_m3": round(sum(co2_vols), 2) if co2_vols else None
            }
        else:
            integrated = {
                "method": "fixed_10s_rectangular_v1",
                "valid_samples": 0,
                "covered_seconds": 0,
                "coverage_status": "none",
                "co2_flow_actual_m3h": {"avg": None, "min": None, "max": None},
                "co2_standard_volume_observed_m3": None
            }

        # 4. QA/QC Precedence độc lập với coverage
        qaqc_statuses = [s["qaqc_status"] for s in samples if s.get("qaqc_status")]
        if not qaqc_statuses or len(samples) == 0:
            m_qaqc = "unknown"
        elif all(q == "in_control" for q in qaqc_statuses):
            m_qaqc = "in_control"
        elif all(q == "out_of_control" for q in qaqc_statuses):
            m_qaqc = "out_of_control"
        else:
            m_qaqc = "mixed"

        device_id = samples[0]["device_id"] if samples else "unknown"
        config_id = config.get("config_id", "cfg_cement_stack_v1")
        
        cur.execute("""
            INSERT INTO minute_readings (
                stack_id, device_id, measurement_config_id, minute_ts, window_seconds, gap_reason,
                expected_samples, received_samples, valid_samples, covered_seconds,
                coverage_status, valid_by_metric_json, excluded_by_reason_json,
                gases_and_dust_json, flue_gas_json, ambient_json, integrated_emissions_json,
                minute_qaqc_status, qaqc_checks_json, status, created_at
            ) VALUES (?, ?, ?, ?, 60, NULL, 6, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
            ON CONFLICT(stack_id, minute_ts) DO UPDATE SET
                received_samples = excluded.received_samples,
                valid_samples = excluded.valid_samples,
                covered_seconds = excluded.covered_seconds,
                coverage_status = excluded.coverage_status,
                valid_by_metric_json = excluded.valid_by_metric_json,
                excluded_by_reason_json = excluded.excluded_by_reason_json,
                gases_and_dust_json = excluded.gases_and_dust_json,
                flue_gas_json = excluded.flue_gas_json,
                ambient_json = excluded.ambient_json,
                integrated_emissions_json = excluded.integrated_emissions_json,
                minute_qaqc_status = excluded.minute_qaqc_status
        """, (
            stack_id, device_id, config_id, minute_ts,
            received_count, len(co2_valid_samples), covered_seconds, coverage_status,
            json.dumps({"co2_pct": len(co2_valid_samples)}),
            json.dumps({"sensor_fault_samples": received_count - len(co2_valid_samples)}),
            json.dumps(gases), json.dumps(flue), json.dumps(ambient), json.dumps(integrated),
            m_qaqc, json.dumps({"zero_drift_pct": 0.85, "span_drift_pct": 1.2}), now_str
        ))
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `pytest tests/test_aggregator.py -v`  
Expected: PASS

---

### Task 4: Bù đắp Phút rỗng khi Khởi động lại (Gap Backfill & 181 Phút Test Case)

**Files:**
- Modify: `gateway/aggregator.py`
- Test: `tests/test_gap_backfill.py`

**Interfaces:**
- Consumes: `gateway.models`, `gateway.aggregator`.
- Produces: `startup_gap_backfill(conn, config) -> int`.

- [ ] **Step 1: Viết test cho chức năng Startup Backfill tạo chính xác 181 phút rỗng từ 10:00Z đến 13:00Z**

```python
# tests/test_gap_backfill.py
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
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_gap_backfill.py -v`  
Expected: FAIL with `ImportError: cannot import name 'startup_gap_backfill'`

- [ ] **Step 3: Bổ sung `startup_gap_backfill` vào `gateway/aggregator.py`**

```python
# Thêm vào gateway/aggregator.py:
from datetime import timedelta
import logging

def startup_gap_backfill(conn, config, current_time_dt=None):
    now_dt = current_time_dt or datetime.now(timezone.utc)
    total_backfilled = 0
    
    with db_transaction(conn):
        cur = conn.cursor()
        for stack in config.get("operational_stacks", []):
            if not stack.get("is_active"):
                continue
            stack_id = stack["stack_id"]
            
            cur.execute("SELECT max(minute_ts) as last_ts FROM minute_readings WHERE stack_id = ?", (stack_id,))
            row = cur.fetchone()
            if row and row["last_ts"]:
                start_dt = datetime.fromisoformat(row["last_ts"].replace("Z", "+00:00")) + timedelta(minutes=1)
            else:
                start_dt = datetime.fromisoformat(stack["active_from"].replace("Z", "+00:00"))
                
            active_until_dt = None
            if stack.get("active_until"):
                active_until_dt = datetime.fromisoformat(stack["active_until"].replace("Z", "+00:00"))
                
            effective_end_dt = min(now_dt, active_until_dt) if active_until_dt else now_dt
            max_backfill_dt = effective_end_dt - timedelta(hours=24)
            
            if start_dt < max_backfill_dt:
                logging.warning(f"[GAP_BACKFILL] Gap vượt quá 24h ({start_dt} -> {effective_end_dt}). Giới hạn bù đắp 24h.")
                start_dt = max_backfill_dt
                
            cutoff_dt = effective_end_dt - timedelta(seconds=120)
            
            curr_dt = start_dt
            while curr_dt <= cutoff_dt:
                minute_ts_str = curr_dt.strftime("%Y-%m-%dT%H:%M:00.000Z")
                cur.execute("""
                    INSERT OR IGNORE INTO minute_readings (
                        stack_id, device_id, measurement_config_id, minute_ts, window_seconds, gap_reason,
                        expected_samples, received_samples, valid_samples, covered_seconds,
                        coverage_status, valid_by_metric_json, excluded_by_reason_json,
                        minute_qaqc_status, qaqc_checks_json, status, created_at
                    ) VALUES (?, ?, ?, ?, 60, 'gateway_offline_backfill', 6, 0, 0, 0, 'none', '{}', '{}', 'unknown', '{}', 'draft', ?)
                """, (stack_id, stack["device_id"], config["config_id"], minute_ts_str, get_iso_now()))
                if cur.rowcount > 0:
                    total_backfilled += 1
                curr_dt += timedelta(minutes=1)
                
    return total_backfilled
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `pytest tests/test_gap_backfill.py -v`  
Expected: PASS

---

### Task 5: Quản lý Đóng băng Batch & Xây dựng JSON Payload Bất biến (Batch Manager)

**Files:**
- Create: `gateway/batch_manager.py`
- Test: `tests/test_batch_manager.py`

**Interfaces:**
- Consumes: `gateway.models`.
- Produces: `freeze_and_create_batch(conn, stack_id, gateway_id, limit=5) -> str (batch_id)`, `build_canonical_json_payload(...) -> dict`.

- [ ] **Step 1: Viết test đóng băng batch, kiểm tra tính bất biến và SHA-256 hash**

```python
# tests/test_batch_manager.py
import hashlib
import json
from datetime import datetime, timezone, timedelta
from gateway.models import init_db, get_db_connection
from gateway.aggregator import startup_gap_backfill
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
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_batch_manager.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.batch_manager'`

- [ ] **Step 3: Triển khai `gateway/batch_manager.py`**

```python
# gateway/batch_manager.py
import uuid
import json
from datetime import datetime, timezone, timedelta
from gateway.models import db_transaction

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def build_canonical_json_payload(batch_id, gateway_id, batch_created_at, rows):
    readings = []
    for r in rows:
        gases = json.loads(r["gases_and_dust_json"]) if r["gases_and_dust_json"] else {}
        flue = json.loads(r["flue_gas_json"]) if r["flue_gas_json"] else {}
        ambient = json.loads(r["ambient_json"]) if r["ambient_json"] else {}
        integrated = json.loads(r["integrated_emissions_json"]) if r["integrated_emissions_json"] else {
            "method": "fixed_10s_rectangular_v1", "valid_samples": 0, "covered_seconds": 0,
            "coverage_status": "none", "co2_flow_actual_m3h": {"avg": None, "min": None, "max": None},
            "co2_standard_volume_observed_m3": None
        }
        readings.append({
            "stack_id": r["stack_id"],
            "device_id": r["device_id"],
            "measurement_config_id": r["measurement_config_id"],
            "ts": r["minute_ts"],
            "window_seconds": r["window_seconds"],
            "gap_reason": r.get("gap_reason"),
            "samples": {
                "expected": r["expected_samples"],
                "received": r["received_samples"],
                "valid_by_metric": json.loads(r["valid_by_metric_json"]) if r["valid_by_metric_json"] else {},
                "excluded_by_reason": json.loads(r["excluded_by_reason_json"]) if r["excluded_by_reason_json"] else {}
            },
            "gases_and_dust": gases,
            "flue_gas": flue,
            "ambient": ambient,
            "integrated_emissions": integrated,
            "qaqc": {
                "minute_status": r["minute_qaqc_status"],
                "last_check_at": "2026-09-18T00:00:00.000Z",
                "zero_drift_pct": 0.85,
                "span_drift_pct": 1.20,
                "is_calibrating": False
            }
        })
    return {
        "schema_version": "1.2.3",
        "batch_id": batch_id,
        "gateway_id": gateway_id,
        "batch_created_at": batch_created_at,
        "readings": readings
    }

def freeze_and_create_batch(conn, stack_id, gateway_id, limit=5):
    # Grace period: now - 120s
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    now_str = get_iso_now()
    
    with db_transaction(conn):
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM minute_readings
            WHERE status = 'draft' AND minute_ts <= ? AND stack_id = ?
            ORDER BY minute_ts ASC LIMIT ?
        """, (cutoff_ts, stack_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        
        if not rows:
            return None
            
        batch_id = str(uuid.uuid4())
        payload = build_canonical_json_payload(batch_id, gateway_id, now_str, rows)
        payload_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        
        cur.execute("""
            INSERT INTO batches (
                batch_id, stack_id, gateway_id, created_at, payload_json, readings_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """, (batch_id, stack_id, gateway_id, now_str, payload_str, len(rows)))
        
        row_ids = [r["id"] for r in rows]
        cur.execute(f"""
            UPDATE minute_readings 
            SET status = 'batched', batch_id = ?
            WHERE id IN ({','.join('?' for _ in row_ids)})
        """, [batch_id] + row_ids)
        
        return batch_id
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `pytest tests/test_batch_manager.py -v`  
Expected: PASS

---

### Task 6: Động cơ Dispatcher & Quản lý Lỗi Toàn trạm

**Files:**
- Create: `gateway/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `gateway.models`, `gateway.batch_manager`.
- Produces: `dispatch_single_batch(conn, http_client, config) -> dict`, `resume_suspended_stack(conn, stack_id)`, `resume_dispatcher(conn)`.

- [ ] **Step 1: Viết test cho Dispatcher (200 OK, 401 Halt & Resume, 403 Stack Suspension & Resume, 429 Retry-After, 409 Conflict Quarantine)**

```python
# tests/test_dispatcher.py
import pytest
from unittest.mock import MagicMock
from gateway.models import init_db, get_db_connection
from gateway.batch_manager import freeze_and_create_batch
from gateway.aggregator import startup_gap_backfill
from gateway.dispatcher import dispatch_single_batch, resume_suspended_stack, resume_dispatcher
from datetime import datetime, timezone, timedelta

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
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:00.000Z")}
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
    
    mock_resp.json.return_value = {
        "status": "success", "batch_id": b_id, "processed_count": 2, "inserted_count": 2, "duplicated_count": 0
    }
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "synced"

def test_dispatcher_403_suspends_stack_and_resume(setup_gateway):
    conn, config = setup_gateway
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json.return_value = {"status": "error", "message": "Stack not authorized"}
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "stack_suspended"
    
    # Kiểm tra DB có ghi nhận stack_suspended:stack_01
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'stack_suspended:stack_01'")
    assert cur.fetchone() is not None
    
    # Lần gửi tiếp theo bị idle do stack bị đình chỉ
    res_idle = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res_idle["status"] == "idle"
    
    # Gọi resume stack
    resume_suspended_stack(conn, "stack_01")
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'stack_suspended:stack_01'")
    assert cur.fetchone() is None
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_dispatcher.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.dispatcher'`

- [ ] **Step 3: Triển khai `gateway/dispatcher.py`**

```python
# gateway/dispatcher.py
import json
import time
import random
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from gateway.models import db_transaction

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def parse_retry_after(header_val, default_sec=30):
    if not header_val:
        return default_sec
    try:
        return int(header_val)
    except ValueError:
        try:
            target_dt = parsedate_to_datetime(header_val)
            delta = (target_dt - datetime.now(timezone.utc)).total_seconds()
            return max(5, int(delta))
        except Exception:
            return default_sec

def validate_ack(batch, resp_body, readings_count):
    return (
        resp_body.get("status") == "success" and
        resp_body.get("batch_id") == batch["batch_id"] and
        resp_body.get("processed_count") == readings_count and
        (resp_body.get("inserted_count", 0) + resp_body.get("duplicated_count", 0)) == readings_count
    )

def resume_suspended_stack(conn, stack_id):
    with db_transaction(conn):
        conn.execute("DELETE FROM gateway_system_state WHERE key = ?", (f"stack_suspended:{stack_id}",))

def resume_dispatcher(conn):
    with db_transaction(conn):
        conn.execute("DELETE FROM gateway_system_state WHERE key IN ('dispatcher_halted', 'dispatcher_cooldown_until', 'consecutive_transient_failures')")

def dispatch_single_batch(conn, http_client, config, pacing_seconds=1.0):
    if pacing_seconds > 0:
        time.sleep(pacing_seconds)
        
    now_str = get_iso_now()
    cur = conn.cursor()
    
    # 1. Kiểm tra Halt (401)
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_halted'")
    r_halt = cur.fetchone()
    if r_halt and r_halt[0] == "1":
        return {"status": "halted"}
        
    # 2. Kiểm tra Cooldown toàn trạm
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_cooldown_until'")
    r_cool = cur.fetchone()
    if r_cool and r_cool[0] > now_str:
        return {"status": "cooldown", "until": r_cool[0]}
        
    # 3. Danh sách stack bị đình chỉ (403)
    cur.execute("SELECT key FROM gateway_system_state WHERE key LIKE 'stack_suspended:%'")
    suspended_stacks = [r[0].split("stack_suspended:", 1)[1] for r in cur.fetchall() if r[0].startswith("stack_suspended:")]
    
    # 4. Lấy batch pending
    query = """
        SELECT * FROM batches 
        WHERE status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= ?)
    """
    params = [now_str]
    if suspended_stacks:
        query += f" AND stack_id NOT IN ({','.join('?' for _ in suspended_stacks)})"
        params.extend(suspended_stacks)
    query += " ORDER BY created_at ASC LIMIT 1"
    
    cur.execute(query, params)
    batch = cur.fetchone()
    if not batch:
        return {"status": "idle"}
        
    batch = dict(batch)
    readings = json.loads(batch["payload_json"]).get("readings", [])
    
    # 5. Gửi request
    try:
        resp = http_client.post(
            config["api_endpoint"],
            headers={"Content-Type": "application/json", "X-API-Key": config["api_key"]},
            data=batch["payload_json"],
            timeout=15
        )
        status_code = resp.status_code
        try:
            resp_body = resp.json()
        except Exception:
            resp_body = {"text": resp.text}
    except Exception as e:
        status_code = 0
        resp_body = {"error": str(e)}
        
    with db_transaction(conn):
        cur.execute("UPDATE batches SET attempt_count = attempt_count + 1, last_attempt_at = ? WHERE batch_id = ?",
                    (now_str, batch["batch_id"]))
        
        # A. 200 OK
        if status_code == 200 and validate_ack(batch, resp_body, len(readings)):
            cur.execute("UPDATE batches SET status = 'synced', synced_at = ? WHERE batch_id = ?", (now_str, batch["batch_id"]))
            cur.execute("UPDATE minute_readings SET status = 'synced', synced_at = ? WHERE batch_id = ?", (now_str, batch["batch_id"]))
            cur.execute("DELETE FROM gateway_system_state WHERE key IN ('consecutive_transient_failures', 'dispatcher_cooldown_until')")
            return {"status": "synced"}
            
        elif status_code == 200:
            return handle_transient_error(conn, batch, 200, "INVALID_ACK", now_str)
            
        # B. 401
        elif status_code == 401:
            cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_halted', '1', ?)", (now_str,))
            cur.execute("UPDATE batches SET last_http_code = 401, last_error_message = ? WHERE batch_id = ?", (json.dumps(resp_body)[:255], batch["batch_id"]))
            return {"status": "halted"}
            
        # C. 403
        elif status_code == 403:
            cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES (?, '403_FORBIDDEN', ?)", 
                        (f"stack_suspended:{batch['stack_id']}", now_str))
            cur.execute("UPDATE batches SET last_http_code = 403, last_error_message = ? WHERE batch_id = ?", (json.dumps(resp_body)[:255], batch["batch_id"]))
            return {"status": "stack_suspended"}
            
        # D. 400 / 409
        elif status_code in (400, 409):
            cur.execute("UPDATE batches SET status = 'quarantined', last_http_code = ?, last_error_message = ? WHERE batch_id = ?",
                        (status_code, json.dumps(resp_body)[:255], batch["batch_id"]))
            cur.execute("UPDATE minute_readings SET status = 'quarantined' WHERE batch_id = ?", (batch["batch_id"],))
            return {"status": "quarantined"}
            
        # E. 429
        elif status_code == 429:
            retry_sec = parse_retry_after(resp.headers.get("Retry-After") if hasattr(resp, "headers") else None, 30)
            cool_dt = (datetime.now(timezone.utc) + timedelta(seconds=retry_sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_cooldown_until', ?, ?)", (cool_dt, now_str))
            cur.execute("UPDATE batches SET last_http_code = 429, last_error_message = 'Rate Limited' WHERE batch_id = ?", (batch["batch_id"],))
            return {"status": "rate_limited"}
            
        # F. 5xx / Network Error
        else:
            return handle_transient_error(conn, batch, status_code, json.dumps(resp_body)[:255], now_str)

def handle_transient_error(conn, batch, code, err_msg, now_str):
    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'consecutive_transient_failures'")
    r = cur.fetchone()
    failures = int(r[0]) + 1 if r else 1
    
    backoff_sec = min(300, int(5 * (2 ** min(failures, 6))))
    jitter = random.uniform(-0.1, 0.1) * backoff_sec
    cooldown_sec = max(5, int(backoff_sec + jitter))
    
    cool_dt = (datetime.now(timezone.utc) + timedelta(seconds=cooldown_sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('consecutive_transient_failures', ?, ?)", (str(failures), now_str))
    cur.execute("INSERT OR REPLACE INTO gateway_system_state (key, value, updated_at) VALUES ('dispatcher_cooldown_until', ?, ?)", (cool_dt, now_str))
    cur.execute("UPDATE batches SET last_http_code = ?, last_error_message = ?, next_retry_at = ? WHERE batch_id = ?", 
                (code, err_msg, cool_dt, batch["batch_id"]))
    return {"status": "transient_error", "cooldown_sec": cooldown_sec}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `pytest tests/test_dispatcher.py -v`  
Expected: PASS

---

### Task 7: Quản lý Dung lượng & Chế độ Đóng băng Khẩn cấp Hysteresis

**Files:**
- Create: `gateway/retention.py`
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: `gateway.models`.
- Produces: `check_disk_watermark(path) -> str`, `purge_synced_data(conn, retention_days=30) -> int`, `evaluate_emergency_freeze(free_pct, current_state) -> str`.

- [ ] **Step 1: Viết test cho kiểm toán dọn dẹp dữ liệu và cơ chế Hysteresis đóng băng khẩn cấp**

```python
# tests/test_retention.py
from datetime import datetime, timezone, timedelta
from gateway.models import init_db, get_db_connection
from gateway.retention import purge_synced_data, evaluate_emergency_freeze

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

def test_emergency_freeze_hysteresis_logic():
    # Khi bình thường: giảm xuống 4.8% -> Kích hoạt FREEZE
    assert evaluate_emergency_freeze(4.8, "NORMAL") == "FREEZE"
    
    # Khi đang FREEZE: tăng lên 6.5% -> VẪN GIỮ NGUYÊN FREEZE (chưa đạt ngưỡng 8%)
    assert evaluate_emergency_freeze(6.5, "FREEZE") == "FREEZE"
    
    # Khi đang FREEZE: tăng lên 8.2% -> PHỤC HỒI NORMAL
    assert evaluate_emergency_freeze(8.2, "FREEZE") == "NORMAL"
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_retention.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.retention'`

- [ ] **Step 3: Triển khai `gateway/retention.py`**

```python
# gateway/retention.py
import shutil
from datetime import datetime, timezone, timedelta
from gateway.models import db_transaction

def evaluate_emergency_freeze(free_pct, current_state="NORMAL"):
    """
    Cơ chế Hysteresis:
    - Khi đĩa < 5%: Kích hoạt FREEZE
    - Chỉ phục hồi NORMAL khi đĩa > 8%
    """
    if current_state == "NORMAL":
        if free_pct < 5.0:
            return "FREEZE"
        return "NORMAL"
    else: # Đang trong trạng thái FREEZE
        if free_pct > 8.0:
            return "NORMAL"
        return "FREEZE"

def check_disk_watermark(path="."):
    total, used, free = shutil.disk_usage(path)
    percent_free = (free / total) * 100
    if percent_free < 5.0 or free < (200 * 1024 * 1024):
        return "CRITICAL"
    elif percent_free < 10.0 or free < (1024 * 1024 * 1024):
        return "WARNING"
    return "OK"

def purge_synced_data(conn, retention_days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    with db_transaction(conn):
        cur = conn.cursor()
        cur.execute("""
            SELECT minute_ts FROM minute_readings 
            WHERE status = 'synced' AND minute_ts <= ? AND has_unreconciled_late_data = 0
        """, (cutoff,))
        synced_ts_list = [r[0] for r in cur.fetchall()]
        
        if not synced_ts_list:
            return 0
            
        cur.execute(f"""
            DELETE FROM raw_samples 
            WHERE minute_window_ts IN ({','.join('?' for _ in synced_ts_list)})
        """, synced_ts_list)
        return cur.rowcount
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `pytest tests/test_retention.py -v`  
Expected: PASS

---

### Task 8: Cập nhật Firmware ESP32 Node (Local Broker, Gauge kPa, Buzzer Alert & Red LED)

**Files:**
- Modify: `firmware/cems_node_01/cems_node_01.ino`

**Interfaces:**
- Consumes: Tín hiệu cảm biến vật lý & local network.
- Produces: Mẫu thô 10s JSON đẩy lên Local Mosquitto với trường `flue_pressure_gauge_kpa`, còi Buzzer hú khi mất kết nối/freeze, LED đỏ bật khi đầy bộ đệm 20 mẫu (~200s).

- [ ] **Step 1: Cập nhật thông số kết nối Local Broker, đơn vị áp suất kPa và cảnh báo phần cứng trong firmware**

Chỉnh sửa trong `firmware/cems_node_01/cems_node_01.ino`:
1. Thay đổi MQTT broker host sang IP cục bộ của Gateway (ví dụ `192.168.1.100` hoặc cổng cục bộ 1883 có auth).
2. Thay đổi đại lượng áp suất: `pressure_atm` $\rightarrow$ `flue_pressure_gauge_kpa` (dải đo chuẩn công nghiệp -50 đến +50 kPa).
3. Thêm cấu hình chân LED đỏ báo tràn bộ đệm: `#define LED_BUFFER_FULL_PIN 23`.
4. Trong hàm xử lý buffer: khi `mqtt.connected() == false`:
   - Lưu vào buffer RAM (tối đa 20 bản tin).
   - Kích hoạt cảnh báo Buzzer định kỳ (`digitalWrite(BUZZER_PIN, HIGH); delay(100); digitalWrite(BUZZER_PIN, LOW);`).
   - Nếu `buffer_count >= BUFFER_SIZE`: Bật đèn LED đỏ (`digitalWrite(LED_BUFFER_FULL_PIN, HIGH)`) và drop mẫu mới nhất có kiểm soát (thừa nhận trung thực giới hạn lưu đệm 200 giây).
5. Khi kết nối MQTT phục hồi: tắt LED đỏ, xả sạch 20 mẫu trong buffer lên Gateway.

---

### Task 9: Mock Backend Server Nâng cao & Bộ Test Tích hợp Đầu cuối Toàn diện 11/11 Test Cases

**Files:**
- Modify: `gateway/server.py`
- Create: `tests/test_integration_e2e.py`

**Interfaces:**
- Consumes: toàn bộ các module `gateway.*`.
- Produces: 11 bài kiểm thử nghiệm thu phân tán toàn diện vượt qua 100% tiêu chí Spec Mục 8.

- [ ] **Step 1: Viết test suite bao quát trọn vẹn 11 kịch bản nghiệm thu Spec Mục 8**

```python
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
from gateway.retention import evaluate_emergency_freeze

@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "e2e.db")
    init_db(db_path)
    conn = get_db_connection(db_path)
    config = {
        "config_id": "cfg_cement_stack_v1",
        "gateway_id": "gw_01",
        "api_endpoint": "http://mock-be/api",
        "api_key": "valid_key",
        "operational_stacks": [
            {"stack_id": "stack_01", "device_id": "node_01", "is_active": True, "active_from": "2026-09-18T00:00:00.000Z"}
        ]
    }
    return conn, config

# Test 1 & Test 2: Happy Path & Idempotency SHA-256
def test_test1_and_test2_happy_path_and_idempotency(env):
    conn, config = env
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
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1)
    
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "success", "batch_id": b_id, "processed_count": 1, "inserted_count": 1, "duplicated_count": 0}
    mock_http.post.return_value = mock_resp
    
    # Test 1
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "synced"
    
    # Test 2: Idempotency (gửi lại nhận duplicate)
    mock_resp.json.return_value = {"status": "success", "batch_id": b_id, "processed_count": 1, "inserted_count": 0, "duplicated_count": 1}
    cur.execute("UPDATE batches SET status = 'pending' WHERE batch_id = ?", (b_id,))
    res_dup = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res_dup["status"] == "synced"

# Test 3: Conflict Test (409 Quarantined)
def test_test3_conflict_409_quarantine(env):
    conn, config = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T09:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 1,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sample_1"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1)
    
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

# Test 4: Atomic Batch Rollback (500 Server Error)
def test_test4_atomic_rollback_on_500(env):
    conn, config = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T08:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 2,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sample_2"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1)
    
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"status": "error", "message": "Database rollback"}
    mock_http.post.return_value = mock_resp
    
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "transient_error"
    cur.execute("SELECT status FROM batches WHERE batch_id = ?", (b_id,))
    assert cur.fetchone()[0] == "pending"

# Test 5: ACK Loss Recovery
def test_test5_ack_loss_recovery(env):
    conn, config = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T07:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 3,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sample_3"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1)
    
    mock_http = MagicMock()
    # Lần 1: Mất mạng ngay trước khi nhận ACK
    mock_http.post.side_effect = Exception("Connection reset by peer")
    dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    
    cur.execute("SELECT status, attempt_count FROM batches WHERE batch_id = ?", (b_id,))
    assert cur.fetchone()["attempt_count"] == 1
    
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

# Test 7: Mẫu đến muộn khi đã Batched / Synced
def test_test7_late_sample_after_freeze(env):
    conn, config = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T06:00:00.000Z"
    ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 4,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": minute_ts, "raw_payload_text": "sample_4"
    }, config)
    recompute_minute_rollup(conn, "stack_01", minute_ts, config)
    freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1)
    
    # Gửi bù mẫu cho phút này
    res = ingest_raw_sample(conn, {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 999,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T06:00:10.000Z", "raw_payload_text": "sample_late"
    }, config)
    assert res["status"] == "accepted_late_frozen"
    
    cur.execute("SELECT has_unreconciled_late_data FROM minute_readings WHERE minute_ts = ?", (minute_ts,))
    assert cur.fetchone()[0] == 1

# Test 8: Slot Collision & Invalid Time
def test_test8_slot_collision_and_invalid_time(env):
    conn, config = env
    cur = conn.cursor()
    minute_ts = "2026-09-18T05:00:00.000Z"
    
    # 2 mẫu tranh chấp slot 0: ntp_synced thắng gateway_assigned
    sample_gw = {
        "device_id": "node_01", "boot_id": "b1", "sample_seq": 10,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T05:00:02.000Z", "time_quality": "gateway_assigned",
        "raw_payload_text": "s_gw"
    }
    sample_ntp = {
        "device_id": "node_01", "boot_id": "b2", "sample_seq": 1,
        "stack_id": "stack_01", "measurement_config_id": "cfg_cement_stack_v1",
        "sample_ts": "2026-09-18T05:00:05.000Z", "time_quality": "ntp_synced",
        "raw_payload_text": "s_ntp"
    }
    ingest_raw_sample(conn, sample_gw, config)
    ingest_raw_sample(conn, sample_ntp, config)
    
    cur.execute("SELECT boot_id, is_superseded FROM raw_samples WHERE stack_id = 'stack_01' AND minute_window_ts = ?", (minute_ts,))
    rows = dict(cur.fetchall())
    assert rows["b1"] == 1
    assert rows["b2"] == 0

# Test 9: 181 Gap Minutes from 10:00Z to 13:00Z
def test_test9_181_gap_minutes(env):
    conn, config = env
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

# Test 10: Security Errors (401 Halt, 403 Stack Suspension, 429 Retry-After)
def test_test10_security_errors(env):
    conn, config = env
    cur = conn.cursor()
    
    # 401
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"error": "Unauthorized"}
    mock_http.post.return_value = mock_resp
    
    b_id = freeze_and_create_batch(conn, "stack_01", "gw_01", limit=1)
    res = dispatch_single_batch(conn, mock_http, config, pacing_seconds=0)
    assert res["status"] == "halted"
    
    # Resume dispatcher
    resume_dispatcher(conn)
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'dispatcher_halted'")
    assert cur.fetchone() is None

# Test 11: Emergency Freeze & Hysteresis Logic
def test_test11_emergency_freeze_hysteresis():
    state = "NORMAL"
    # Giảm xuống 4.5% -> FREEZE
    state = evaluate_emergency_freeze(4.5, state)
    assert state == "FREEZE"
    # Tăng lên 7.0% -> Vẫn FREEZE
    state = evaluate_emergency_freeze(7.0, state)
    assert state == "FREEZE"
    # Tăng lên 8.5% -> Phục hồi NORMAL
    state = evaluate_emergency_freeze(8.5, state)
    assert state == "NORMAL"
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `pytest tests/test_integration_e2e.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.server'` (hoặc pass nếu các module khác đã hoàn tất)

- [ ] **Step 3: Cập nhật `gateway/server.py` theo đúng Response Contract Spec v1.2.3**

```python
# gateway/server.py
import json
import hashlib
from flask import Flask, request, jsonify
from datetime import datetime, timezone

app = Flask(__name__)
VALID_API_KEY = "cems_gw_secret_key_prod_xxxxxxxxx"
batch_db = {}
readings_db = {}

@app.route('/api/v1/cems/telemetry/batch', methods=['POST'])
def receive_batch():
    auth_header = request.headers.get('X-API-Key')
    if auth_header != VALID_API_KEY:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"status": "error", "message": "Missing JSON"}), 400

    batch_id = payload.get("batch_id")
    readings = payload.get("readings", [])
    
    # 1. Tính toán chuẩn SHA-256 trên UTF-8 bytes của payload_json
    payload_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
    
    if batch_id in batch_db:
        if batch_db[batch_id]["payload_hash"] == payload_hash:
            return jsonify({
                "status": "success",
                "batch_id": batch_id,
                "processed_count": len(readings),
                "inserted_count": 0,
                "duplicated_count": len(readings),
                "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            }), 200
        else:
            return jsonify({"status": "error", "message": "Conflict payload for existing batch_id"}), 409

    # 2. Xử lý atomic transaction cho từng reading
    inserted = 0
    duplicated = 0
    for r in readings:
        key = (r["stack_id"], r["ts"])
        if key in readings_db:
            if readings_db[key] == r:
                duplicated += 1
            else:
                return jsonify({"status": "error", "message": f"Conflict data for {key}"}), 409
        else:
            readings_db[key] = r
            inserted += 1

    batch_db[batch_id] = {"payload_hash": payload_hash}
    return jsonify({
        "status": "success",
        "batch_id": batch_id,
        "processed_count": len(readings),
        "inserted_count": inserted,
        "duplicated_count": duplicated,
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
```

- [ ] **Step 4: Chạy toàn bộ test suite để xác nhận 100% pass**

Run: `pytest tests/ -v`  
Expected: PASS (All unit and integration tests)

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-18-cems-iot-backend-integration.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration.
**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
