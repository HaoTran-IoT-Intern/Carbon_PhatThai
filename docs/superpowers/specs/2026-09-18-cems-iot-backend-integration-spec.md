# TÀI LIỆU ĐẶC TẢ KỸ THUẬT: TÍCH HỢP HỆ THỐNG IOT CEMS & BACKEND PLATFORM
## Hướng 1 — Bên phát thải (Nhà máy / Doanh nghiệp) — Pione Carbon Credit

- **Tác giả**: IoT Engineering Team & Systems Architect
- **Phiên bản tài liệu**: `1.2.3`
- **Ngày cập nhật**: 2026-09-18
- **Trạng thái**: Validated Technical Specification — Sẵn sàng bàn giao Backend & Triển khai Pilot

---

## 1. Bối cảnh & Phân định ranh giới trách nhiệm (Separation of Concerns)

### 1.1 Bối cảnh hệ thống
Hệ thống CEMS Hướng 1 của Pione Global phục vụ đo lường và giám sát phát thải khí thải công nghiệp liên tục tại ống khói nhà máy nhằm:
1. Cung cấp dữ liệu chuỗi thời gian sạch, liên tục, đáng tin cậy phục vụ kiểm toán và tính toán hạn ngạch phát thải (allowance).
2. Làm đầu vào xác thực để định lượng mức giảm phát thải và phát hành tín chỉ carbon.

### 1.2 Phân định ranh giới giữa IoT và Backend
- **Phạm vi của Tầng IoT (Sensor Node & Edge Gateway)**:
  - Thu thập tín hiệu cảm biến định kỳ 10 giây (khí thải: $CO_2$, $SO_2$, $NO_x$, $CO$, $O_2$, $PM$; vật lý: lưu lượng, nhiệt độ, áp suất dư; môi trường: nhiệt độ, độ ẩm).
  - Node kết nối vào **Local Mosquitto MQTT Broker có xác thực (username/password/ACL)** chạy ngay trên Gateway nội bộ nhà xưởng (thay thế cho HiveMQ public broker).
  - Bảo toàn dữ liệu gốc thu được tại biên (`raw_samples`) trong vòng đời lưu trữ 30 ngày phục vụ đối soát kiểm toán (Audit Trail).
  - Phân bổ mẫu vào 6 khe thời gian (Slots) cố định không chồng lấn mỗi phút; giải quyết xung đột khe dựa trên chuỗi ưu tiên kỹ thuật; tính toán thống kê (avg, min, max, độ phủ thời gian `covered_seconds`).
  - Bảo vệ tuyệt đối tính bất biến của các phút đã đóng băng (`batched`, `synced`, hoặc `quarantined`): mẫu đến muộn không được phép thay đổi mẫu thắng cuộc đã tham gia tạo payload.
  - Phân loại rõ ràng trạng thái trễ của mẫu:
    - `is_late = 0`: Đến đúng hạn ($arrived\_at \le window\_end$).
    - `is_late = 1`: Đến trong grace period ($window\_end < arrived\_at \le window\_end + grace$) khi phút là `draft` $\rightarrow$ cho phép tính lại.
    - `is_late = 2`: Đến sau khi hết grace period hoặc sau khi phút đã đóng băng $\rightarrow$ khóa bất biến, chỉ audit.
    - `is_late = 3`: Mẫu có timestamp lỗi/invalid $\rightarrow$ chỉ lưu raw audit, không gán slot, không vào rollup.
  - Kiểm tra điều kiện vật lý trước khi quy đổi áp suất sang áp suất tuyệt đối $P_{flue\_abs\_kpa} = P_{flue\_gauge\_kpa} + P_{baro\_kpa}$.
  - Tự động bù đắp các phút rỗng (Gap Minutes) cả ở runtime (`gap_reason: "node_no_data"`) và khi Gateway khởi động lại sau sự cố mất điện (`gap_reason: "gateway_offline_backfill"`) với mốc `effective_end = min(now, active_until)`, có cảnh báo khi khoảng trống vượt quá 24 giờ.
  - Đóng gói theo lô (Micro-batch 5 phút), nạp dữ liệu an toàn lên Backend qua HTTPS, hỗ trợ lưu trữ đệm (Store-and-Forward) và xả nghẽn (Backlog Drain) với cơ chế Cooldown toàn trạm, đình chỉ stack khi 403, và hỗ trợ lệnh Resume quản trị.
  - **Chế độ Emergency Freeze có kiểm soát**: Dừng Local Mosquitto với cơ chế Hysteresis (dừng $<5\%$, phục hồi $>8\%$). Đảm bảo hiển hiện sự cố qua còi hú Buzzer và LED đỏ trên Node; bảo vệ CSDL SQLite không bị corrupt. Thừa nhận giới hạn: bộ đệm Node là 20 mẫu (~200s), ngoài 200s Node sẽ drop mẫu mới có kiểm soát kèm còi báo động.
- **Phạm vi của Tầng Backend (Cloud Platform)**:
  - Định nghĩa và sở hữu phiên bản cấu hình đo đạc `measurement_config_id`.
  - Quản lý xác thực Gateway (`X-API-Key`) và ma trận phân quyền theo từng `factory_id`, `stack_id`.
  - Tiếp nhận lô dữ liệu, kiểm tra tính toàn vẹn và chống trùng lặp cấp độ Batch (bằng hàm băm SHA-256 trên UTF-8 bytes của `payload_json`) và Reading (Two-tier Idempotency).
  - **Sở hữu 100% công thức nghiệp vụ Carbon Credit**: Nhân tỷ trọng $\rho_{CO2}$ tính khối lượng phát thải ($kg / tCO_2e$), đối soát hạn ngạch, bù trừ phát thải cơ sở (Baseline) và khớp lệnh tín chỉ carbon.

---

## 2. Cấu trúc Cấu hình Đo đạc & Bảo mật MQTT

### 2.1 Cấu hình Đo đạc (`measurement_config_id`)
```json
{
  "config_id": "cfg_cement_stack_v1",
  "version": "1.2.3",
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
      "active_from": "2026-08-01T00:00:00.000Z",
      "active_until": null
    }
  ]
}
```

### 2.2 Cấu hình Bảo mật Local Mosquitto (`gateway/mosquitto.conf`)
```text
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
```
**Quy tắc ACL (`/etc/mosquitto/acl`)**:
- User `cems_node`: chỉ được phép `topic write pione/carbon/v1/+/data` và `topic read pione/carbon/v1/control/#`.
- User `gateway_internal`: có quyền đọc toàn bộ `topic read pione/carbon/v1/#`.

---

## 3. Kiến trúc tổng thể & Luồng dữ liệu (Data Pipeline)

```
[ ỐNG KHÓI: ESP32 NODE ]
  - Lấy mẫu cảm biến mỗi 10 giây (áp suất đo theo gauge kPa)
  - Định danh duy nhất mẫu: (device_id, boot_id, sample_seq)
  - MA-5 chỉ chạy nội bộ phục vụ LCD 16x2
  - Publish MẪU THÔ sang Local Mosquitto Broker trên Gateway qua WiFi/LAN nội bộ
    kèm thông tin xác thực (username/password) và time_quality bắt buộc
            │
            ▼ (Mỗi 10s: Mẫu thô + Cờ QA/QC + time_quality)
[ NHÀ MÁY: EDGE GATEWAY ]
  - Chạy Local Mosquitto MQTT Broker (port 1883, có ACL).
  1. Ingestion & Freeze Guard: 
     - Nếu phút là 'batched', 'synced' hoặc 'quarantined' -> khóa bất biến, is_superseded = 1, is_late = 2.
     - Nếu time_quality == 'invalid' -> lưu raw audit, slot_index = NULL, is_superseded = 1, is_late = 3.
     - Nếu time_quality == 'gateway_assigned' -> gán sample_ts = arrived_at.
     - Nếu phút là draft và time hợp lệ:
       + arrived_at <= window_end -> is_late = 0
       + window_end < arrived_at <= window_end + grace -> is_late = 1, cho phép tính lại
       + arrived_at > window_end + grace -> is_late = 2, is_superseded = 1, chỉ audit
  2. Gap Detection & Backfill: Tạo phút rỗng runtime và backfill tới effective_end = min(now, active_until),
     lưu rõ gap_reason.
  3. Aggregation: Gom cửa sổ 1 phút [T, T+60s), áp dụng Grace Period: minute_ts <= now - 120s.
  4. Batch Freezing: Cứ mỗi 5 phút, đóng băng các bản ghi phút của cùng 1 stack_id vào `batches`.
  5. Backlog Drain Engine: Single Dispatcher gửi tuần tự FIFO, quản lý Cooldown toàn trạm,
     xử lý 403 bằng cách tách prefix stack_suspended, lưu trích đoạn lỗi đầy đủ, hỗ trợ lệnh Resume CLI.
  6. Emergency Freeze Worker: Giám sát dung lượng đĩa theo cơ chế Hysteresis:
     - Dừng Mosquitto khi dung lượng trống < 5% (Node kích hoạt buffer 20 mẫu và còi hú).
     - Khôi phục Mosquitto khi dung lượng trống > 8%.
            │
            ▼ HTTPS POST /api/v1/cems/telemetry/batch (Headers: Content-Type, X-API-Key)
[ CLOUD: PIONE BACKEND ]
  - Kiểm tra X-API-Key và xác thực quyền trên `stack_id`.
  - Kiểm tra tính bất biến: So khớp mã băm SHA-256 trên UTF-8 bytes của `payload_json`.
  - Xử lý toàn bộ batch trong một Database Transaction duy nhất.
  - Phản hồi JSON xác nhận chi tiết hoặc mã lỗi kèm body mô tả nguyên nhân.
```

---

## 4. Đặc tả API Contract (Backend REST Interface)

### 4.1 Giao thức & Endpoint
- **Method**: `POST`
- **URL**: `https://<backend-domain>/api/v1/cems/telemetry/batch`
- **Headers**:
  ```http
  Content-Type: application/json
  X-API-Key: cems_gw_secret_key_prod_xxxxxxxxx
  ```

### 4.2 Request Payload Schema (Bất biến 100%)

```json
{
  "schema_version": "1.2.3",
  "batch_id": "8f3b6c2a-9e1d-4d7a-b28f-7c1e5a3d9b4c",
  "gateway_id": "gw_ha_tien_01",
  "batch_created_at": "2026-09-18T10:05:00.000Z",
  "readings": [
    {
      "stack_id": "stack_lo_nung_01",
      "device_id": "cems_node_01",
      "measurement_config_id": "cfg_cement_stack_v1",
      "ts": "2026-09-18T10:00:00.000Z",
      "window_seconds": 60,
      "gap_reason": null,
      
      "samples": {
        "expected": 6,
        "received": 6,
        "valid_by_metric": {
          "co2_pct": 6,
          "so2_ppm": 6,
          "nox_ppm": 6,
          "co_ppm": 6,
          "o2_pct": 6,
          "pm_mgm3": 6,
          "flue_temp_c": 6,
          "flue_pressure_abs_kpa": 6,
          "flue_flow_m3h": 6,
          "ambient_temp_c": 6,
          "ambient_humidity_pct": 6
        },
        "excluded_by_reason": {
          "sensor_fault_samples": 0,
          "calibrating_samples": 0,
          "qaqc_failed_samples": 0
        }
      },
      
      "gases_and_dust": {
        "co2_pct": { "avg": 14.85, "min": 14.20, "max": 15.40, "data_source": "simulated" },
        "so2_ppm": { "avg": 84.30, "min": 78.10, "max": 89.50, "data_source": "simulated" },
        "nox_ppm": { "avg": 119.50, "min": 110.00, "max": 128.20, "data_source": "simulated" },
        "co_ppm":  { "avg": 34.80, "min": 32.00, "max": 38.10, "data_source": "simulated" },
        "o2_pct":  { "avg": 17.90, "min": 17.20, "max": 18.50, "data_source": "simulated" },
        "pm_mgm3": { "avg": 12.10, "min": 9.50,  "max": 14.30, "data_source": "simulated" }
      },
      
      "flue_gas": {
        "temp_c": { "avg": 150.2, "min": 148.5, "max": 152.0, "data_source": "simulated" },
        "pressure_abs_kpa": { 
          "avg": 120.57, "min": 119.55, "max": 122.60, 
          "pressure_type": "absolute", 
          "barometric_source": "assumed_barometric_101.325kpa",
          "data_source": "simulated" 
        },
        "flow_m3h": { "avg": 2145.0, "min": 2100.0, "max": 2210.0, "data_source": "simulated" }
      },
      
      "ambient": {
        "temp_c":       { "avg": 29.5, "min": 29.4, "max": 29.6, "data_source": "measured" },
        "humidity_pct": { "avg": 65.0, "min": 64.0, "max": 66.0, "data_source": "measured" }
      },
      
      "integrated_emissions": {
        "method": "fixed_10s_rectangular_v1",
        "valid_samples": 6,
        "covered_seconds": 60,
        "coverage_status": "full",
        "co2_flow_actual_m3h": { "avg": 318.53, "min": 298.20, "max": 340.34 },
        "co2_standard_volume_observed_m3": 5.31
      },
      
      "qaqc": {
        "minute_status": "in_control",
        "last_check_at": "2026-09-18T00:00:00.000Z",
        "zero_drift_pct": 0.85,
        "span_drift_pct": 1.20,
        "is_calibrating": false
      }
    }
  ]
}
```

#### Quy tắc ngữ nghĩa dữ liệu:
1. **Định dạng thời gian**: Toàn bộ dùng UTC ISO 8601 kết thúc bằng `Z` với 3 chữ số mili-giây (`YYYY-MM-DDTHH:mm:ss.sssZ`).
2. **Xử lý thiếu mẫu**: Nếu một đại lượng không có mẫu hợp lệ nào trong phút, giá trị `avg`, `min`, `max` bắt buộc là `null`, tuyệt đối không thay bằng số `0`.
3. **Phút rỗng (Gap Minute)**: Khi trạm đang hoạt động nhưng không nhận được mẫu nào trong phút:
   - `gap_reason`: Ghi rõ `"node_no_data"` hoặc `"gateway_offline_backfill"`.
   - `samples.received = 0, valid_samples = 0, covered_seconds = 0, coverage_status = "none"`.
   - `samples.valid_by_metric`: toàn bộ đại lượng gán bằng `0`.
   - Các khối `gases_and_dust`, `flue_gas`, `ambient`: toàn bộ đại lượng đều chứa `{"avg": null, "min": null, "max": null, "data_source": "none"}`.
   - `integrated_emissions`: `co2_flow_actual_m3h: {avg: null, min: null, max: null}`, `co2_standard_volume_observed_m3: null`.
   - `qaqc.minute_status = "unknown"`.
4. **Phạm vi của `excluded_by_reason`**: Chỉ đếm các mẫu 10s bị loại khỏi phép tích phân $CO_2$.
5. **Ma trận trạng thái QA/QC phút (`minute_status`)**:
   - `unknown`: Không có kết quả kiểm tra drift nào hợp lệ trong 24 giờ qua hoặc phút rỗng.
   - `in_control`: Toàn bộ các mẫu có đánh giá drift trong phút đều nằm trong ngưỡng $\pm 2\%$.
   - `out_of_control`: Toàn bộ các mẫu có đánh giá drift trong phút đều vượt ngưỡng $\pm 2\%$.
   - `mixed`: Tồn tại đồng thời mẫu drift đạt và mẫu drift vượt ngưỡng.

---

### 4.3 Phản hồi từ Backend (Response Contract)

#### Phản hồi Thành công (200 OK)
```json
{
  "status": "success",
  "batch_id": "8f3b6c2a-9e1d-4d7a-b28f-7c1e5a3d9b4c",
  "processed_count": 1,
  "inserted_count": 1,
  "duplicated_count": 0,
  "server_time": "2026-09-18T10:05:00.350Z"
}
```

#### Điều kiện xác nhận đồng bộ hợp lệ phía Gateway (Validation Check):
```python
is_valid_ack = (
    resp.status_code == 200 and
    resp_body.get("status") == "success" and
    resp_body.get("batch_id") == batch["batch_id"] and
    resp_body.get("processed_count") == len(readings_in_batch) and
    (resp_body.get("inserted_count", 0) + resp_body.get("duplicated_count", 0)) == len(readings_in_batch)
)
```

#### Ma trận mã lỗi Backend:
| Mã lỗi | Nguyên nhân | Hành vi của Gateway |
|---|---|---|
| **`400 Bad Request`** | JSON sai schema, sai định dạng ISO UTC, giá trị vô lý | Lưu trích đoạn lỗi, cách ly batch (`quarantined`), ngừng retry batch này. |
| **`401 Unauthorized`** | Thiếu hoặc sai `X-API-Key` | Tạm dừng toàn bộ Dispatcher (`HALTED_CREDENTIAL`), ghi log Critical, hỗ trợ lệnh Resume hoặc reload config. |
| **`403 Forbidden`** | API Key hợp lệ nhưng không được gán quyền cho `stack_id` | Đình chỉ riêng hàng đợi của `stack_id` này (`stack_suspended:<stack_id>`), hỗ trợ lệnh Resume Stack. |
| **`409 Conflict`** | Trùng khóa nhưng sai lệch SHA-256 hash của payload | Lưu trích đoạn lỗi, cách ly batch (`quarantined`), lưu lại để kiểm toán thủ công. |
| **`429 Too Many Requests`** | Backend bị quá tải / Rate limit | Đọc header `Retry-After`, lưu trích đoạn lỗi, tạm dừng Dispatcher toàn trạm theo số giây quy định. |
| **`5xx / Timeout / ConnErr`** | Backend bảo trì, sập DB, rớt mạng | Lưu trích đoạn lỗi, kích hoạt Cooldown toàn trạm trong `gateway_system_state`, áp dụng Exponential Backoff có Jitter. |

---

## 5. Thiết kế Database SQLite tại Edge Gateway (`cems_data.db`)

```sql
-- 1. BẢNG MẪU THÔ (RAW SAMPLES)
CREATE TABLE IF NOT EXISTS raw_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    sample_seq INTEGER NOT NULL,
    stack_id TEXT NOT NULL,
    measurement_config_id TEXT NOT NULL,
    
    sample_ts TEXT,                   -- UTC ISO 8601 từ Node (hoặc gán arrived_at nếu gateway_assigned; NULL nếu invalid)
    time_quality TEXT NOT NULL,       -- 'ntp_synced' | 'gateway_assigned' | 'invalid'
    arrived_at TEXT NOT NULL,         -- UTC ISO 8601 khi Gateway nhận
    minute_window_ts TEXT,            -- Cửa sổ phút [YYYY-MM-DDTHH:mm:00.000Z] (NULL nếu invalid)
    slot_index INTEGER,               -- Khe 0..5 trong phút (NULL nếu invalid)
    is_superseded INTEGER DEFAULT 0,  -- 1 nếu bị ghi đè, invalid hoặc đến sau khi phút đã đóng băng/quá grace
    is_late INTEGER DEFAULT 0,        -- 0: đúng hạn, 1: trong grace period, 2: sau đóng băng/quá grace, 3: invalid_ts
    
    -- Đại lượng đo
    co2_pct REAL,
    so2_ppm REAL,
    nox_ppm REAL,
    co_ppm REAL,
    o2_pct REAL,
    pm_mgm3 REAL,
    flue_temp_c REAL,
    flue_pressure_gauge_kpa REAL,     -- Đo trực tiếp theo kPa áp suất dư
    flue_pressure_abs_kpa REAL,       -- Quy đổi: gauge + barometric (NULL nếu sensor lỗi)
    flue_flow_m3h REAL,
    ambient_temp_c REAL,
    ambient_humidity_pct REAL,
    
    -- Quy đổi tức thời (cơ sở ướt)
    instantaneous_co2_flow_m3h REAL,
    instantaneous_co2_std_volume_m3 REAL,
    
    metric_quality_json TEXT NOT NULL,
    qaqc_status TEXT NOT NULL,        -- 'in_control' | 'out_of_control'
    is_calibrating INTEGER DEFAULT 0,
    
    raw_payload_text TEXT NOT NULL,   -- Lưu nguyên vẹn chuỗi payload gốc để đối soát
    created_at TEXT NOT NULL,
    
    UNIQUE(device_id, boot_id, sample_seq)
);
CREATE INDEX IF NOT EXISTS idx_raw_slot ON raw_samples(stack_id, minute_window_ts, slot_index, is_superseded);

-- 2. BẢNG XUNG ĐỘT MẪU THÔ (RAW INGEST CONFLICTS)
CREATE TABLE IF NOT EXISTS raw_ingest_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    sample_seq INTEGER NOT NULL,
    existing_payload TEXT NOT NULL,
    conflicting_payload TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

-- 3. BẢNG TỔNG HỢP PHÚT (MINUTE READINGS)
CREATE TABLE IF NOT EXISTS minute_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stack_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    measurement_config_id TEXT NOT NULL,
    minute_ts TEXT NOT NULL,          -- Đầu phút UTC [YYYY-MM-DDTHH:mm:00.000Z]
    window_seconds INTEGER DEFAULT 60,
    gap_reason TEXT,                  -- 'node_no_data' | 'gateway_offline_backfill' | NULL
    
    -- Thống kê độ phủ
    expected_samples INTEGER DEFAULT 6,
    received_samples INTEGER DEFAULT 0,
    valid_samples INTEGER DEFAULT 0,
    covered_seconds INTEGER DEFAULT 0,
    coverage_status TEXT NOT NULL,    -- 'full' | 'partial' | 'none'
    valid_by_metric_json TEXT NOT NULL,
    excluded_by_reason_json TEXT NOT NULL,
    
    -- Dữ liệu đo lường
    gases_and_dust_json TEXT,
    flue_gas_json TEXT,
    ambient_json TEXT,
    integrated_emissions_json TEXT,
    
    -- QA/QC phút
    minute_qaqc_status TEXT NOT NULL, -- 'in_control' | 'out_of_control' | 'mixed' | 'unknown'
    qaqc_checks_json TEXT NOT NULL,
    
    -- Cờ đối soát nội bộ (Không gửi lên payload Cloud)
    has_unreconciled_late_data INTEGER DEFAULT 0,
    
    -- Vòng đời
    batch_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft', -- 'draft' | 'batched' | 'synced' | 'quarantined'
    created_at TEXT NOT NULL,
    synced_at TEXT,
    
    UNIQUE(stack_id, minute_ts)
);
CREATE INDEX IF NOT EXISTS idx_minute_status ON minute_readings(status, minute_ts);

-- 4. BẢNG QUẢN LÝ LÔ TRUYỀN (BATCHES)
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,        -- UUID v4 bất biến
    stack_id TEXT NOT NULL,           -- 1 batch chỉ thuộc 1 stack duy nhất
    gateway_id TEXT NOT NULL,
    created_at TEXT NOT NULL,        -- UTC ISO 8601 cố định
    payload_json TEXT NOT NULL,      -- JSON bất biến 100%
    payload_sha256 TEXT NOT NULL,    -- Mã băm SHA-256 trên UTF-8 bytes của payload_json
    readings_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'synced' | 'quarantined'
    
    attempt_count INTEGER DEFAULT 0,
    last_attempt_at TEXT,
    last_http_code INTEGER,
    last_error_message TEXT,
    next_retry_at TEXT,
    synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_batch_status ON batches(status, stack_id, next_retry_at);

-- 5. BẢNG TRẠNG THÁI HỆ THỐNG DISPATCHER (SYSTEM STATE)
CREATE TABLE IF NOT EXISTS gateway_system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

## 6. Quy tắc vận hành biên & Thuật toán xử lý (Edge Logic)

### 6.1 Chuỗi ưu tiên Slot Collision & Phân định is_late
1. **Kiểm tra trạng thái phút trước**:
   - Nếu bản ghi phút tương ứng có `status IN ('batched', 'synced', 'quarantined')`: Phút đã đóng băng. Mẫu mới đến **BẮT BUỘC gán `is_superseded = 1` và `is_late = 2`**, tuyệt đối không tham gia slot collision và không sửa rollup.
2. **Nếu phút là `draft`**:
   - Cửa sổ thời gian: $window\_end = minute\_ts + 60s$; Ngưỡng ân hạn: $grace\_end = minute\_ts + 120s$.
   - Nếu $arrived\_at > grace\_end$: Mẫu đã quá hạn ân hạn $\rightarrow$ Gán `is_late = 2, is_superseded = 1`, chỉ lưu raw audit, không đưa vào rollup.
   - Nếu $arrived\_at \le grace\_end$:
     - Nếu $arrived\_at \le window\_end \rightarrow is\_late = 0$ (đúng hạn).
     - Nếu $window\_end < arrived\_at \le grace\_end \rightarrow is\_late = 1$ (trong ân hạn, cho phép recompute).
     - Nếu `time_quality == 'invalid'`: Bắt buộc gán `is_superseded = 1, is_late = 3, minute_window_ts = NULL, slot_index = NULL`, chỉ lưu raw phục vụ audit, không cho vào rollup.
     - Nếu `time_quality == 'gateway_assigned'`: Gán $sample\_ts = arrived\_at$.
     - Giải quyết xung đột khe $k$ theo thang bậc ưu tiên:
       1. `time_quality`: `ntp_synced (3) > gateway_assigned (2) > invalid (1)`.
       2. Độ hợp lệ của bộ đại lượng cốt lõi ($CO_2$, Flow, Temp, Pressure).
       3. Trạng thái hiệu chuẩn: `is_calibrating == 0` thắng `is_calibrating == 1`.
       4. Tie-breaker: Mẫu có `arrived_at` sớm hơn thắng.

Mẫu thắng cuộc giữ `is_superseded = 0`, mẫu thua cuộc nhận `is_superseded = 1`.
$$covered\_seconds = \text{COUNT}(\text{DISTINCT slots occupied by non-superseded valid samples}) \times 10\text{s} \le 60\text{s}$$

### 6.2 Xử lý Ingestion Mẫu Thô & Trùng lặp
```python
def ingest_raw_sample(db, raw_data, config):
    now_str = get_iso_now()
    arrived_dt = datetime.now(timezone.utc)
    
    with db.transaction(): # BEGIN IMMEDIATE
        # 1. Kiểm tra duplicate raw
        existing = db.get("""
            SELECT id, raw_payload_text FROM raw_samples 
            WHERE device_id = ? AND boot_id = ? AND sample_seq = ?
        """, (raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"]))
        
        if existing:
            if existing["raw_payload_text"] == raw_data["raw_payload_text"]:
                return {"status": "duplicate_ignored"}
            else:
                db.insert("raw_ingest_conflicts", {
                    "device_id": raw_data["device_id"],
                    "boot_id": raw_data["boot_id"],
                    "sample_seq": raw_data["sample_seq"],
                    "existing_payload": existing["raw_payload_text"],
                    "conflicting_payload": raw_data["raw_payload_text"],
                    "detected_at": now_str
                })
                return {"status": "conflict_rejected"}
                
        # 2. Xử lý time_quality và parse timestamp an toàn
        time_qual = raw_data.get("time_quality")
        if not time_qual in ("ntp_synced", "gateway_assigned", "invalid"):
            time_qual = "invalid" # Bắt buộc phải khai báo, nếu thiếu hoặc sai -> invalid
            
        slot_k = None
        minute_window_ts = raw_data.get("minute_window_ts")
        is_valid_time = False
        sample_ts = raw_data.get("sample_ts")
        
        if time_qual == "gateway_assigned":
            sample_ts = now_str
            sample_dt = arrived_dt
            slot_k = int(sample_dt.second // 10)
            minute_window_ts = sample_dt.strftime("%Y-%m-%dT%H:%M:00.000Z")
            is_valid_time = True
        elif time_qual == "ntp_synced" and sample_ts:
            try:
                sample_dt = datetime.fromisoformat(sample_ts.replace("Z", "+00:00"))
                slot_k = int(sample_dt.second // 10)
                minute_window_ts = sample_dt.strftime("%Y-%m-%dT%H:%M:00.000Z")
                is_valid_time = True
            except Exception:
                time_qual = "invalid"
                
        # 3. Tính toán áp suất tuyệt đối kPa (chỉ tính khi cảm biến hợp lệ và trong dải vật lý)
        p_gauge = raw_data.get("flue_pressure_gauge_kpa")
        metric_q = raw_data.get("metric_quality", {})
        p_baro = config.get("flue_gas_sensors", {}).get("barometric_pressure_kpa_default", 101.325)
        p_range = config.get("flue_gas_sensors", {}).get("pressure_gauge_valid_range_kpa", [-50.0, 50.0])
        
        p_abs = None
        if (p_gauge is not None and 
            metric_q.get("flue_pressure_gauge_kpa") != "sensor_fault" and 
            p_range[0] <= p_gauge <= p_range[1] and 
            p_gauge > -900.0):
            p_abs = round(p_gauge + p_baro, 2)
        
        # 4. Kiểm tra trạng thái rollup của phút này
        if is_valid_time:
            minute_row = db.get("SELECT id, status FROM minute_readings WHERE stack_id = ? AND minute_ts = ?", 
                                (raw_data["stack_id"], minute_window_ts))
            is_frozen = minute_row is not None and minute_row["status"] in ("batched", "synced", "quarantined")
            window_end_dt = datetime.fromisoformat(minute_window_ts.replace("Z", "+00:00")) + timedelta(seconds=60)
            grace_end_dt = window_end_dt + timedelta(seconds=60)
            
            if is_frozen or arrived_dt > grace_end_dt:
                is_late = 2
                is_superseded = 1
            elif arrived_dt > window_end_dt:
                is_late = 1
                is_superseded = 0
            else:
                is_late = 0
                is_superseded = 0
                
            if not is_frozen and arrived_dt <= grace_end_dt:
                current_slot_sample = db.get("""
                    SELECT id, time_quality, metric_quality_json, is_calibrating, arrived_at 
                    FROM raw_samples 
                    WHERE stack_id = ? AND minute_window_ts = ? AND slot_index = ? AND is_superseded = 0
                """, (raw_data["stack_id"], minute_window_ts, slot_k))
                
                if current_slot_sample:
                    if compare_slot_priority(raw_data, current_slot_sample) > 0:
                        db.execute("UPDATE raw_samples SET is_superseded = 1 WHERE id = ?", (current_slot_sample["id"],))
                    else:
                        is_superseded = 1
        else:
            is_frozen = False
            is_late = 3 # invalid_timestamp
            is_superseded = 1
            minute_row = None
            slot_k = None
            minute_window_ts = None
            
        # 5. Lưu vào raw_samples
        db.insert("raw_samples", {
            **raw_data,
            "sample_ts": sample_ts,
            "time_quality": time_qual,
            "slot_index": slot_k,
            "minute_window_ts": minute_window_ts,
            "flue_pressure_abs_kpa": p_abs,
            "is_superseded": is_superseded,
            "is_late": is_late,
            "arrived_at": raw_data.get("arrived_at", now_str),
            "created_at": now_str
        })
        
        # 6. Cập nhật rollup
        if is_valid_time:
            if is_frozen or is_late == 2:
                if minute_row:
                    db.execute("UPDATE minute_readings SET has_unreconciled_late_data = 1 WHERE id = ?", (minute_row["id"],))
            else:
                recompute_minute_rollup(db, raw_data["stack_id"], minute_window_ts, config)
                
    return {"status": "accepted"}
```

### 6.3 Thuật toán Bù đắp Phút rỗng khi Khởi động lại (Startup Gap Backfill)
```python
def startup_gap_backfill(db, config):
    now_dt = datetime.now(timezone.utc)
    for stack in config["operational_stacks"]:
        if not stack.get("is_active"):
            continue
        stack_id = stack["stack_id"]
        
        # Tính effective_end chuẩn xác theo active_until
        effective_end_dt = now_dt
        if stack.get("active_until"):
            until_dt = datetime.fromisoformat(stack["active_until"].replace("Z", "+00:00"))
            effective_end_dt = min(now_dt, until_dt)
            
        cutoff_dt = effective_end_dt - timedelta(seconds=120)
        max_backfill_dt = effective_end_dt - timedelta(hours=24)
        
        last_row = db.get("SELECT max(minute_ts) as last_ts FROM minute_readings WHERE stack_id = ?", (stack_id,))
        if last_row and last_row["last_ts"]:
            start_dt = datetime.fromisoformat(last_row["last_ts"].replace("Z", "+00:00")) + timedelta(minutes=1)
        else:
            start_dt = datetime.fromisoformat(stack["active_from"].replace("Z", "+00:00"))
            
        # Kiểm tra giới hạn 24 giờ tính theo effective_end
        if start_dt < max_backfill_dt:
            log_critical(f"[BACKFILL_TRUNCATED] Stack {stack_id} mất điện vượt quá 24h. Chỉ backfill từ {max_backfill_dt.isoformat()}.")
            db.set_val("gateway_system_state", f"backfill_truncated:{stack_id}", json.dumps({
                "truncated_from": start_dt.isoformat(),
                "truncated_to": max_backfill_dt.isoformat(),
                "detected_at": get_iso_now()
            }))
            start_dt = max_backfill_dt
            
        curr_dt = start_dt
        
        empty_gases = json.dumps({
            "co2_pct": {"avg": None, "min": None, "max": None, "data_source": "none"},
            "so2_ppm": {"avg": None, "min": None, "max": None, "data_source": "none"},
            "nox_ppm": {"avg": None, "min": None, "max": None, "data_source": "none"},
            "co_ppm":  {"avg": None, "min": None, "max": None, "data_source": "none"},
            "o2_pct":  {"avg": None, "min": None, "max": None, "data_source": "none"},
            "pm_mgm3": {"avg": None, "min": None, "max": None, "data_source": "none"}
        })
        empty_flue = json.dumps({
            "temp_c": {"avg": None, "min": None, "max": None, "data_source": "none"},
            "pressure_abs_kpa": {"avg": None, "min": None, "max": None, "pressure_type": "absolute", "data_source": "none"},
            "flow_m3h": {"avg": None, "min": None, "max": None, "data_source": "none"}
        })
        empty_ambient = json.dumps({
            "temp_c": {"avg": None, "min": None, "max": None, "data_source": "none"},
            "humidity_pct": {"avg": None, "min": None, "max": None, "data_source": "none"}
        })
        empty_integrated = json.dumps({
            "method": "fixed_10s_rectangular_v1", "valid_samples": 0, "covered_seconds": 0,
            "coverage_status": "none", "co2_flow_actual_m3h": {"avg": None, "min": None, "max": None},
            "co2_standard_volume_observed_m3": None
        })
        empty_valid_metric = json.dumps({
            "co2_pct": 0, "so2_ppm": 0, "nox_ppm": 0, "co_ppm": 0, "o2_pct": 0, "pm_mgm3": 0,
            "flue_temp_c": 0, "flue_pressure_abs_kpa": 0, "flue_flow_m3h": 0, "ambient_temp_c": 0, "ambient_humidity_pct": 0
        })
        
        while curr_dt <= cutoff_dt:
            minute_ts_str = curr_dt.strftime("%Y-%m-%dT%H:%M:00.000Z")
            db.execute("""
                INSERT OR IGNORE INTO minute_readings (
                    stack_id, device_id, measurement_config_id, minute_ts, window_seconds, gap_reason,
                    expected_samples, received_samples, valid_samples, covered_seconds,
                    coverage_status, valid_by_metric_json, excluded_by_reason_json,
                    gases_and_dust_json, flue_gas_json, ambient_json, integrated_emissions_json,
                    minute_qaqc_status, qaqc_checks_json, status, created_at
                ) VALUES (?, ?, ?, ?, 60, 'gateway_offline_backfill', 6, 0, 0, 0, 'none', ?, '{}', ?, ?, ?, ?, 'unknown', '{}', 'draft', ?)
            """, (stack_id, stack["device_id"], config["config_id"], minute_ts_str, 
                  empty_valid_metric, empty_gases, empty_flue, empty_ambient, empty_integrated, get_iso_now()))
            curr_dt += timedelta(minutes=1)
```

### 6.4 Thuật toán Dispatcher & Quản trị Lỗi
```python
def resume_suspended_stack(db, stack_id):
    """Lệnh quản trị: Khôi phục hàng đợi của stack sau khi sửa quyền 403"""
    db.delete_val("gateway_system_state", f"stack_suspended:{stack_id}")
    log_info(f"[RESUME_STACK] Đã khôi phục stack {stack_id}.")

def resume_dispatcher(db):
    """Lệnh quản trị: Khôi phục Dispatcher sau khi cập nhật API Key 401"""
    db.delete_val("gateway_system_state", "dispatcher_halted")
    log_info("[RESUME_DISPATCHER] Đã khôi phục toàn bộ Dispatcher.")

def dispatcher_loop(db, http_client, config_file):
    config = load_config(config_file)
    
    while True:
        now_str = get_iso_now()
        time.sleep(1.0) # Pacing tối thiểu 1.0 giây giữa mọi lần request
        
        # 1. Tự động resume 401 nếu API key trong config.json đổi
        new_cfg = load_config(config_file)
        if new_cfg.get("api_key") != config.get("api_key"):
            config = new_cfg
            db.delete_val("gateway_system_state", "dispatcher_halted")
            log_info("[AUTO_RESUME] Phát hiện API Key mới. Khôi phục Dispatcher.")
            
        # 2. Kiểm tra cờ dừng khẩn cấp (401 Auth Revoked)
        if db.get_val("gateway_system_state", "dispatcher_halted") == "1":
            time.sleep(10)
            continue
            
        # 3. Kiểm tra Cooldown toàn trạm
        cooldown_until = db.get_val("gateway_system_state", "dispatcher_cooldown_until")
        if cooldown_until and cooldown_until > now_str:
            time.sleep(2)
            continue
            
        # 4. Lấy danh sách stack bị đình chỉ (Tách prefix chuẩn xác)
        raw_keys = db.get_keys_by_prefix("gateway_system_state", "stack_suspended:")
        suspended_stacks = [k.replace("stack_suspended:", "", 1) for k in raw_keys]
        
        # 5. Chọn batch pending cũ nhất thuộc stack hợp lệ
        query = """
            SELECT * FROM batches 
            WHERE status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= ?)
        """
        params = [now_str]
        if suspended_stacks:
            query += f" AND stack_id NOT IN ({','.join('?' for _ in suspended_stacks)})"
            params.extend(suspended_stacks)
        query += " ORDER BY created_at ASC LIMIT 1"
        
        batch = db.query_one(query, params)
        if not batch:
            time.sleep(2)
            continue
            
        # 6. Gửi HTTP POST
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
                resp_body = {"raw_text": resp.text[:500]}
        except Exception as e:
            status_code = 0
            resp_body = {"error": str(e)}
            
        # 7. Xử lý phản hồi trong Transaction SQLite ngắn
        readings_in_batch = json.loads(batch["payload_json"]).get("readings", [])
        
        with db.transaction():
            db.execute("UPDATE batches SET attempt_count = attempt_count + 1, last_attempt_at = ? WHERE batch_id = ?", 
                       (now_str, batch["batch_id"]))
            
            # A. 200 OK và ACK hợp lệ
            if status_code == 200 and validate_ack(batch, resp_body, len(readings_in_batch)):
                db.execute("UPDATE batches SET status = 'synced', synced_at = ? WHERE batch_id = ?", (now_str, batch["batch_id"]))
                db.execute("UPDATE minute_readings SET status = 'synced', synced_at = ? WHERE batch_id = ?", (now_str, batch["batch_id"]))
                db.delete_val("gateway_system_state", "consecutive_transient_failures")
                db.delete_val("gateway_system_state", "dispatcher_cooldown_until")
                
            # B. 200 OK nhưng ACK không hợp lệ (Invalid ACK)
            elif status_code == 200:
                log_critical(f"[INVALID_ACK] Backend trả 200 nhưng nội dung ACK không khớp: {resp_body}")
                handle_transient_failure(db, batch, 200, json.dumps(resp_body)[:255], now_str)
                
            # C. 401 Unauthorized -> Dừng toàn bộ Dispatcher
            elif status_code == 401:
                log_critical(f"[AUTH] API Key bị từ chối! Dừng toàn bộ Dispatcher. Body: {resp_body}")
                db.set_val("gateway_system_state", "dispatcher_halted", "1")
                db.execute("UPDATE batches SET last_http_code = 401, last_error_message = ? WHERE batch_id = ?", 
                           (json.dumps(resp_body)[:255], batch["batch_id"]))
                
            # D. 403 Forbidden -> Đình chỉ riêng stack này
            elif status_code == 403:
                log_error(f"[FORBIDDEN] Gateway không có quyền trên stack {batch['stack_id']}. Đình chỉ stack. Body: {resp_body}")
                db.set_val("gateway_system_state", f"stack_suspended:{batch['stack_id']}", "403_FORBIDDEN")
                db.execute("UPDATE batches SET last_http_code = 403, last_error_message = ? WHERE batch_id = ?", 
                           (json.dumps(resp_body)[:255], batch["batch_id"]))
                
            # E. 400 / 409 Conflict -> Cách ly batch
            elif status_code in (400, 409):
                log_critical(f"[DATA_ERROR] Lỗi {status_code}. Cách ly batch {batch['batch_id']}. Body: {resp_body}")
                db.execute("UPDATE batches SET status = 'quarantined', last_http_code = ?, last_error_message = ? WHERE batch_id = ?", 
                           (status_code, json.dumps(resp_body)[:255], batch["batch_id"]))
                db.execute("UPDATE minute_readings SET status = 'quarantined' WHERE batch_id = ?", (batch["batch_id"],))
                
            # F. 429 Too Many Requests
            elif status_code == 429:
                retry_sec = parse_retry_after(resp.headers.get("Retry-After") if hasattr(resp, "headers") else None, 30)
                cooldown_until = (datetime.now(timezone.utc) + timedelta(seconds=retry_sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                db.set_val("gateway_system_state", "dispatcher_cooldown_until", cooldown_until)
                db.execute("UPDATE batches SET last_http_code = 429, last_error_message = 'Rate Limited' WHERE batch_id = ?", 
                           (json.dumps(resp_body)[:255], batch["batch_id"]))
                
            # G. Lỗi mạng / 5xx
            else:
                handle_transient_failure(db, batch, status_code, json.dumps(resp_body)[:255], now_str)
```

---

## 7. Quản lý Dung lượng & Chế độ Khẩn cấp (Data Retention & Emergency Freeze)

1. **Nguyên tắc bảo toàn**:  
   - Mọi dữ liệu thô (`raw_samples`) và tổng hợp phút (`minute_readings`) được bảo toàn trọn vẹn trong vòng đời lưu trữ 30 ngày.
   - **TUYỆT ĐỐI KHÔNG XÓA** các bản ghi chưa đồng bộ (`draft`, `batched`, `pending`), các bản ghi bị cách ly (`quarantined`), hoặc các bản ghi có cờ đối soát `has_unreconciled_late_data = 1`.
2. **Quy trình dọn dẹp định kỳ (Automated Purge Job)**:  
   Chạy mỗi 24 giờ một lần, chỉ xóa các mẫu thô thuộc về bản ghi phút có `status = 'synced'` và cũ hơn 30 ngày.
3. **Chế độ đóng băng khẩn cấp với ngưỡng Hysteresis (Emergency Disk Freeze Worker)**:
   - **Mục đích**: Bảo vệ CSDL SQLite không bị ghi tràn làm hỏng file dữ liệu, và làm sự cố hiển hiện ra hiện trường (còi hú, LED đỏ) thay vì âm thầm mất dữ liệu.
   - **Ngưỡng Hysteresis**:
     - Khi dung lượng đĩa trống **$< 5\%$ (hoặc $< 200\text{ MB}$)**: Gateway chuyển sang trạng thái `DISK_EMERGENCY_FREEZE`. Thực thi lệnh hệ thống `systemctl stop mosquitto` (chặn cổng 1883).
     - Chỉ khi dung lượng đĩa trống được giải phóng **$> 8\%$ (hoặc $> 500\text{ MB}$)**: Gateway mới thực thi lệnh `systemctl start mosquitto` để tiếp tục nhận dữ liệu.
   - **Tác động & Giới hạn tại Node ESP32**:
     - Khi cổng 1883 bị chặn, ESP32 phát hiện mất kết nối MQTT (`mqtt.connected() == false`), lập tức chuyển sang lưu vào bộ đệm RAM (tối đa 20 bản tin, tương đương ~200 giây) và **bật còi Buzzer tại chỗ** cảnh báo sự cố kết nối.
     - **Thừa nhận kỹ thuật trung thực**: Nếu sự cố đầy đĩa kéo dài quá 200 giây, bộ đệm Node sẽ đầy. Lúc này Node sẽ drop mẫu mới theo cơ chế FIFO/drop và bật đèn LED đỏ báo tràn bộ đệm. Sự cố không thể bị che giấu.
   - **Ghi log trong lúc đóng băng**: Toàn bộ sự kiện dừng/khôi phục broker được ghi tạm vào file text `/var/log/cems_emergency_freeze.log`, sau khi CSDL mở lại sẽ được flush vào bảng `gateway_system_state`.

---

## 8. Tiêu chí nghiệm thu tích hợp (Acceptance Criteria & Test Matrix)

Hệ thống được nghiệm thu khi vượt qua **11 bài kiểm thử phân tán toàn diện**:

- [ ] **Test 1: Luồng chuẩn (Happy Path)**  
  Gateway đẩy thành công batch 5 phút gồm 5 bản ghi 1-phút lên Backend. Backend phản hồi `200 OK`, CSDL Backend ghi nhận đủ 5 bản ghi, CSDL SQLite Gateway cập nhật `status = 'synced'` cho toàn bộ lô.
- [ ] **Test 2: Kháng trùng lặp an toàn (Idempotency Test)**  
  Gửi lại nguyên vẹn batch của Test 1. Backend đối chiếu mã băm SHA-256 trên UTF-8 bytes của `payload_json` trùng khớp -> Phản hồi `200 OK` với `duplicated_count = 5`, không ghi đè hay nhân đôi phát thải.
- [ ] **Test 3: Phát hiện xung đột dữ liệu (Conflict Test)**  
  Gửi lại `batch_id` cũ nhưng cố tình sửa giá trị `co2_pct` (sai lệch SHA-256 hash) -> Backend từ chối với mã `409 Conflict`, Gateway lưu trích đoạn lỗi và cách ly lô vào trạng thái `quarantined`.
- [ ] **Test 4: Giao dịch nguyên tử toàn Batch (Atomic Batch Rollback Test)**  
  Backend giả lập lỗi cơ sở dữ liệu khi ghi bản ghi thứ 3 trong 5 bản ghi -> Toàn bộ transaction bị rollback trên Backend, không có bản ghi nào lưu vào DB, Backend trả 500 kèm JSON thông báo lỗi -> Gateway lưu error message và giữ nguyên batch để retry.
- [ ] **Test 5: Mất ACK & Khôi phục (ACK Loss Recovery)**  
  Backend commit thành công vào database nhưng ngắt kết nối mạng ngay trước khi gửi HTTP 200 về Gateway. Gateway retry cùng `batch_id` -> Backend trả `200 OK` (duplicate) -> Gateway cập nhật `synced` an toàn.
- [ ] **Test 6: Gateway Crash & Restart Recovery**  
  Tắt nguồn cưỡng bức Gateway (`SIGKILL`) khi đang có batch `batched` và `pending`. Khởi động lại -> Gateway nạp lại trạng thái, tôn trọng `dispatcher_cooldown_until`, không gửi đúp hay mất mát bản tin.
- [ ] **Test 7: Mẫu đến muộn khi đã Batched, Synced hoặc Quarantined**  
  Gửi bù mẫu 10s thuộc về phút đã đóng băng (`batched`, `synced`, `quarantined`). Gateway ghi nhận vào `raw_samples` với `is_superseded = 1` và `is_late = 2`, bật cờ `has_unreconciled_late_data = 1` trong SQLite nội bộ, **tuyệt đối không sửa** mẫu thắng cuộc đã nằm trong batch.
- [ ] **Test 8: Xung đột khe mẫu qua Reboot & Mẫu Invalid (Slot Collision & Invalid Time Test)**  
  - Gửi 2 mẫu có cùng slot thời gian nhưng khác `boot_id`. Mẫu 1 có `ntp_synced`, mẫu 2 có `gateway_assigned`. Gateway áp dụng chuỗi ưu tiên kỹ thuật, chọn đúng mẫu 1, gán `is_superseded = 1` cho mẫu 2.
  - Gửi mẫu có `time_quality = 'invalid'` và chuỗi `sample_ts = 'invalid_ts'`. Gateway không bị crash, lưu raw an toàn với `slot_index = NULL, minute_window_ts = NULL, is_superseded = 1, is_late = 3`.
- [ ] **Test 9: Phút rỗng & Khởi động lại Bù đắp Gap Xác định (Deterministic Gap Backfill Test)**  
  - Phút cuối cùng trong DB là `2026-09-18T09:59:00.000Z`.
  - Gateway khởi động lại tại mốc `2026-09-18T13:02:00.000Z`.
  - Ngưỡng đủ điều kiện đóng băng (Cutoff) là `13:00:00.000Z` (`13:02:00Z - 120s`).
  - Thuật toán `startup_gap_backfill` tự động tạo chính xác **181 bản ghi phút rỗng** (từ `10:00:00.000Z` đến `13:00:00.000Z` inclusive) với `gap_reason = 'gateway_offline_backfill'` và các metric là `null`.
- [ ] **Test 10: Xử lý sự cố mạng & Mã lỗi bảo mật (401, 403, 429, 5xx)**  
  - Gửi sai API Key -> Nhận `401`, Dispatcher lưu error message và dừng khẩn cấp (`HALTED`). Sau khi đổi key trong `config.json` (hoặc gọi lệnh resume), Dispatcher tự động resume.
  - Gửi sai stack -> Nhận `403`, stack bị đình chỉ trong `gateway_system_state`, các stack khác vẫn chạy bình thường. Gọi `resume_suspended_stack()` khôi phục stack thành công.
  - Giả lập `429` kèm `Retry-After: 15` -> Dispatcher lưu error message và hoãn toàn trạm đúng 15 giây.
  - Giả lập `503` -> Toàn bộ Dispatcher áp dụng Cooldown tăng dần có Jitter.
- [ ] **Test 11: Đóng băng Khẩn cấp Ổ đĩa & Ngưỡng Hysteresis (Emergency Disk Freeze & Recovery Test)**  
  - Giả lập dung lượng đĩa giảm xuống $< 5\%$ -> Worker kích hoạt `stop mosquitto`, chuyển trạng thái `DISK_EMERGENCY_FREEZE`.
  - Node mất kết nối MQTT -> Kích hoạt bộ đệm RAM 20 mẫu và bật còi Buzzer.
  - Tăng dung lượng đĩa lên $> 8\%$ -> Worker kích hoạt `start mosquitto`.
  - Node kết nối lại và gửi bù thành công 20 mẫu buffer.
