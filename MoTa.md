# 📋 BẢN TỔNG HỢP KỸ THUẬT: HỆ THỐNG CEMS IOT (HƯỚNG 1)
**Phục vụ: Đo đạc phát thải & Kiểm toán Tín chỉ Carbon (Pione Carbon Platform)**  
**Trạng thái hệ thống: `ĐÃ KIỂM THỬ HOÀN TẤT (100% GREEN) — SẴN SÀNG BÀN GIAO`**  
**Phiên bản Hợp đồng Kỹ thuật: `Spec v1.2.3 — Validated Technical Specification`**

---

### 1. Kiến trúc Tổng thể & Dòng chảy Dữ liệu (End-to-End Flow)

```
[ CẢM BIẾN & ESP32 NODE ] 
     │ (Mẫu thô 10s: CO2, SO2, NOx, CO, O2, Bụi, Lưu lượng, Áp suất dư, Nhiệt độ, DHT11)
     │ (Thời gian: Đồng bộ NTP UTC chuẩn quốc tế)
     ▼ (MQTT qua Wi-Fi nội bộ xưởng)
[ LOCAL MOSQUITTO BROKER ] (Chạy trên Edge Gateway tại nhà máy - Port 1883)
     │
     ▼ (Lắng nghe topic: pione/carbon/v1/+/data)
[ EDGE GATEWAY DAEMON ] (Python Engine chạy trên máy tính Gateway)
     │ ├── 1. Slot Manager: Chia 6 khe/phút, giải quyết xung đột, chống ghi đè phút đã chốt.
     │ ├── 2. Aggregator: Tích phân thể tích CO2 chuẩn ẩm (0°C, 101.325 kPa), xử lý QA/QC.
     │ ├── 3. Gap Backfill: Tự bù đắp phút rỗng khi sập nguồn/mất điện.
     │ ├── 4. Batch Manager: Đóng băng lô 5 phút bất biến (Micro-batch 1-10 phút).
     │ └── 5. Dispatcher: Quản lý hàng đợi đẩy lô có Retry & Cooldown.
     ▼ (HTTPS POST mỗi 5 phút + Header X-API-Key)
[ BACKEND PLATFORM ] (Cloud Server hoặc Server Nội bộ)
     ├── 1. Xác thực Gateway & Chống trùng lặp 2 tầng (Two-tier Idempotency).
     ├── 2. Trả về biên nhận xác nhận (HTTP 200 OK + ACK Token).
     └── 3. Sở hữu công thức Carbon: Nhân tỷ trọng ρCO2, tính tCO2e, cấp Tín chỉ Carbon.
```

---

### 2. Các Hạng Mục Kỹ Thuật Đã Hoàn Thành & Khắc Phục Triệt Để

| Hạng mục | Vấn đề ban đầu | Giải pháp kỹ thuật đã triển khai |
| :--- | :--- | :--- |
| **Firmware ESP32** | Thiếu trường Data Contract, dùng Broker public HiveMQ. | Cập nhật gửi đầy đủ 100% trường dữ liệu thô 10s; chuyển sang kết nối **Mosquitto nội bộ** có bảo mật; thêm còi Buzzer và LED đỏ cảnh báo. |
| **I2C & Watchdog ESP32** | Treo Watchdog (`TG1WDT_SYS_RESET`) do xung đột GPIO Flash 8/9. | Chuyển chân I2C về **GPIO 21 (SDA) / GPIO 22 (SCL)**; thêm cơ chế quét I2C có Timeout (100ms) tự động nhận diện cả LCD `0x27` và `0x3F`, chống sập nguồn khi mất màn hình. |
| **Bộ đệm Store-and-Forward** | Nguy cơ tràn RAM khi mất mạng kéo dài. | Cài đặt Ring Buffer 20 mẫu (~200s); khi đệm đầy tự động drop mẫu mới có kiểm soát, sáng LED đỏ và hú còi báo động. Khi có mạng tự động xả dồn 100% mẫu đọng. |
| **Edge Gateway Daemon** | File cũ chạy PoC v2 đơn giản. | Viết mới hoàn toàn **Production Gateway Daemon (`gateway/gateway.py`)** chạy đa luồng an toàn, tích hợp Slot Manager, Aggregator, Batch Manager, Dispatcher, Retention Watchdog. |
| **Thuật toán Tích phân CO2** | Sai lệch cơ sở khí khô/ướt và mẫu lỗi. | Tích phân theo mẫu 10s ở điều kiện chuẩn ẩm ($0^\circ\text{C}, 101.325\text{ kPa}$); tự động lọc mẫu hiệu chuẩn (`is_calibrating=1`) và mẫu lỗi cảm biến (`qaqc_status="out_of_control"`). |
| **An toàn Đĩa cứng (Emergency Freeze)** | SQLite dễ hỏng khi đầy đĩa. | Cơ chế Hysteresis: Đĩa $<5\%$ tự dừng Broker và hú còi cảnh báo; đĩa $>8\%$ tự phục hồi; tự động dọn dẹp (purge) dữ liệu cũ đã đồng bộ sau 30 ngày. |

---

### 3. Bằng Chứng Kiểm Thử Thực Tế (Verification Evidence)

1. **Kiểm thử Tự động (Automated Test Suite)**:
   - **73 / 73 Tests PASSED (100%)** bao phủ toàn bộ 11 kịch bản End-to-End (Idempotency, 409 Conflict, 401 Halt, 403 Suspended Stack, 429 Rate-limit, 500 Retry, Gap Minutes, Emergency Freeze).
2. **Kiểm thử Phần cứng & Truyền nhận Sống (Live Hardware Verification)**:
   - Đã nạp code vào mạch ESP32 thật, đọc nhiệt độ/độ ẩm phòng thật qua DHT11 (`27.5°C, 46.8%`).
   - Đã đồng bộ giờ NTP quốc tế và truyền bản tin 10s đều đặn qua Mosquitto Broker.
   - Gateway đã tiếp nhận mẫu thật vào SQLite (`cems_data.db`), tổng hợp đủ 6/6 mẫu mỗi phút và đã đóng băng đồng bộ thành công hơn **150 lô dữ liệu (`synced`)** lên Mock Backend.

---

### 4. Hợp Đồng Giao Tiếp & Đặc Tả Dữ Liệu Bàn Giao Cho Team Backend

Team Backend chỉ cần triển khai **1 Endpoint duy nhất**:

* **Endpoint**: `POST /api/v1/cems/telemetry/batch`
* **Headers**:
  ```http
  Content-Type: application/json
  X-API-Key: <cems_gw_secret_key>
  ```
* **Quy tắc phản hồi từ Backend**:
  - **`HTTP 200 OK`**: Trả về `{"status": "success", "batch_id": "...", "processed_count": N, "ack_token": "..."}` để Gateway đánh dấu `synced`.
  - **`HTTP 400`**: Dữ liệu sai định dạng $\rightarrow$ Gateway cách ly lô (`quarantined`), không retry.
  - **`HTTP 401`**: Sai API Key $\rightarrow$ Gateway dừng đẩy toàn trạm.
  - **`HTTP 403`**: Trạm bị khóa $\rightarrow$ Gateway đình chỉ riêng ống khói vi phạm.
  - **`HTTP 409`**: Xung đột payload $\rightarrow$ Gateway cách ly lô.
  - **`HTTP 429`**: Quá tải $\rightarrow$ Gateway chờ theo `Retry-After`.
  - **`HTTP 500 / 503`**: Lỗi tạm thời $\rightarrow$ Gateway tự động lùi thời gian thử lại (Exponential Backoff).

* **Phân định ranh giới tính toán**:
  - **IoT Gateway gửi**: Thể tích $CO_2$ chuẩn ẩm tích phân theo phút (`co2_standard_volume_m3`).
  - **Backend sở hữu công thức**: Nhân tỷ trọng $\rho_{CO2} \approx 1.977\text{ }kg/m^3 \rightarrow$ khối lượng phát thải $kg \rightarrow$ tấn $tCO_2e \rightarrow$ Tín chỉ Carbon:
    $$M_{CO2} (kg) = co2\_standard\_volume\_m3 \times 1.977 \longrightarrow tCO_2e = \frac{M_{CO2}}{1000}$$

---

### 5. Từ Điển Dữ Liệu Chi Tiết (Data Dictionary Cho Database Của Backend)

Dưới đây là danh sách toàn bộ các trường trong gói JSON 5 phút do Gateway gửi lên, được nhóm theo bảng cơ sở dữ liệu để Team BE thiết kế Database:

#### 5.1. Bảng Lô Gửi (`cems_telemetry_batches`)
| Tên trường JSON | Kiểu dữ liệu | Bắt buộc | Diễn giải ý nghĩa |
| :--- | :--- | :---: | :--- |
| `schema_version` | `VARCHAR(16)` | Có | Phiên bản hợp đồng dữ liệu, cố định là `"1.2.3"`. |
| `batch_id` | `UUID / VARCHAR(64)` | Có | **Khóa chính duy nhất của Lô** (UUIDv4) do Gateway sinh ra. |
| `gateway_id` | `VARCHAR(64)` | Có | Mã định danh thiết bị Gateway tại trạm (vd: `"gw_ha_tien_01"`). |
| `batch_created_at` | `TIMESTAMPTZ` | Có | Thời điểm đóng băng gói dữ liệu theo chuẩn ISO-8601 UTC. |
| `readings` | `JSON / ARRAY` | Có | Mảng chứa từ 1 đến 10 bản ghi phút (xem bảng 5.2). |

#### 5.2. Bảng Dữ Liệu Phút Chi Tiết (`cems_minute_readings`)
Mỗi phần tử trong mảng `readings` đại diện cho **1 phút đo đạc hoàn chỉnh**:

##### A. Thông tin định danh & thời gian
| Tên trường JSON | Kiểu dữ liệu | Diễn giải ý nghĩa & Đơn vị |
| :--- | :--- | :--- |
| `stack_id` | `VARCHAR(64)` | Mã ống khói (vd: `"stack_01"` hoặc `"stack_lo_nung_01"`). |
| `device_id` | `VARCHAR(64)` | Mã thiết bị đo ESP32 Node (vd: `"cems_node_01"`). |
| `measurement_config_id` | `VARCHAR(64)` | Phiên bản cấu hình đo đạc (vd: `"cfg_cement_stack_v1"`). |
| `minute_ts` | `TIMESTAMPTZ` | **Thời điểm bắt đầu của phút đo** (vd: `"2026-09-21T10:00:00.000Z"`). Khóa Unique: `(stack_id, minute_ts)`. |
| `window_seconds` | `INTEGER` | Độ dài cửa sổ đo, mặc định `60` giây. |
| `gap_reason` | `VARCHAR(64)` | Lý do nếu là phút rỗng (`null` nếu bình thường, `"node_no_data"`, hoặc `"gateway_offline_backfill"`). |

##### B. Chất lượng & Số lượng mẫu (`samples`)
| Tên trường JSON | Kiểu dữ liệu | Diễn giải ý nghĩa & Đơn vị |
| :--- | :--- | :--- |
| `samples.expected` | `INTEGER` | Số mẫu kỳ vọng trong 1 phút (mặc định `6` mẫu 10s). |
| `samples.received` | `INTEGER` | Số mẫu thực tế Gateway nhận được (`0` đến `6`). |
| `samples.valid_by_metric` | `JSON` | Số mẫu hợp lệ cho từng chỉ số (`co2`, `flow`, `temp`, `pressure`...). |
| `samples.excluded_by_reason` | `JSON` | Số mẫu bị loại trừ do lỗi cảm biến hoặc đang hiệu chuẩn. |

##### C. Nhóm Nồng độ Khí & Bụi (`gases_and_dust`)
| Tên trường JSON | Kiểu dữ liệu | Diễn giải ý nghĩa & Đơn vị |
| :--- | :--- | :--- |
| `gases_and_dust.co2_pct.avg / min / max` | `DECIMAL(6,2)` | Nồng độ khí $CO_2$ trong phút (Đơn vị: **`%` thể tích**). |
| `gases_and_dust.so2_ppm.avg / min / max` | `DECIMAL(8,2)` | Nồng độ khí $SO_2$ trong phút (Đơn vị: **`ppm`**). |
| `gases_and_dust.nox_ppm.avg / min / max` | `DECIMAL(8,2)` | Nồng độ khí $NO_x$ trong phút (Đơn vị: **`ppm`**). |
| `gases_and_dust.co_ppm.avg / min / max` | `DECIMAL(8,2)` | Nồng độ khí $CO$ trong phút (Đơn vị: **`ppm`**). |
| `gases_and_dust.o2_pct.avg / min / max` | `DECIMAL(6,2)` | Nồng độ khí $O_2$ trong phút (Đơn vị: **`%` thể tích**). |
| `gases_and_dust.pm_mg_m3.avg / min / max`| `DECIMAL(8,2)` | Nồng độ Bụi tổng $PM$ trong phút (Đơn vị: **`mg/m3`**). |

##### D. Nhóm Vật lý Khí thải Ống khói (`flue_gas`)
| Tên trường JSON | Kiểu dữ liệu | Diễn giải ý nghĩa & Đơn vị |
| :--- | :--- | :--- |
| `flue_gas.flow_rate_m3_h.avg / min / max`| `DECIMAL(10,2)`| Lưu lượng dòng khí thải qua ống khói (Đơn vị: **`m3/h`**). |
| `flue_gas.temperature_c.avg / min / max` | `DECIMAL(6,2)` | Nhiệt độ dòng khí thải trong ống khói (Đơn vị: **`°C`**). |
| `flue_gas.pressure_gauge_kpa.avg / min / max` | `DECIMAL(6,2)` | Áp suất dư (tương đối) trong ống khói (Đơn vị: **`kPa`**). |
| `flue_gas.pressure_abs_kpa.avg / min / max` | `DECIMAL(8,3)` | Áp suất tuyệt đối đã chuẩn hóa: $P_{gauge} + 101.325$ (Đơn vị: **`kPa`**). |

##### E. Nhóm Môi trường Phòng đo (`ambient` - từ DHT11)
| Tên trường JSON | Kiểu dữ liệu | Diễn giải ý nghĩa & Đơn vị |
| :--- | :--- | :--- |
| `ambient.temperature_c.avg / min / max` | `DECIMAL(5,2)` | Nhiệt độ môi trường phòng đặt trạm (Đơn vị: **`°C`**). |
| `ambient.humidity_pct.avg / min / max`  | `DECIMAL(5,2)` | Độ ẩm không khí môi trường (Đơn vị: **`%`**). |

##### F. Nhóm Tích phân Phát thải (**Trọng tâm tính Tín chỉ Carbon** - `integrated_emissions`)
| Tên trường JSON | Kiểu dữ liệu | Diễn giải ý nghĩa & Đơn vị |
| :--- | :--- | :--- |
| `integrated_emissions.co2_standard_volume_m3` | `DECIMAL(12,4)`| **Tổng thể tích $CO_2$ chuẩn sinh ra trong 1 phút** ($0^\circ\text{C}, 101.325\text{ kPa}$ ẩm). Đơn vị: **`m3`**. |
| `integrated_emissions.flue_gas_standard_volume_m3`| `DECIMAL(14,4)`| Tổng thể tích khí thải chuẩn sinh ra trong 1 phút (Đơn vị: **`m3`**). |
| `integrated_emissions.calculation_method` | `VARCHAR(32)` | Phương pháp tính toán: `"sample_discrete_integration"`. |

##### G. Nhóm Kiểm soát Chất lượng & Hiệu chuẩn (`qaqc`)
| Tên trường JSON | Kiểu dữ liệu | Diễn giải ý nghĩa |
| :--- | :--- | :--- |
| `qaqc.minute_status` | `VARCHAR(32)` | Trạng thái phút (`"ok"`, `"suspect"`, `"out_of_control"`). |
| `qaqc.zero_drift_pct`| `DECIMAL(5,2)` | Độ trôi điểm không của cảm biến (Zero drift %). |
| `qaqc.span_drift_pct`| `DECIMAL(5,2)` | Độ trôi điểm span của cảm biến (Span drift %). |
| `qaqc.is_calibrating`| `BOOLEAN` | `true` nếu cảm biến đang trong chu trình tự hiệu chuẩn. |

---

### 6. Mã SQL Tạo Bảng Mẫu Cho Team Backend (PostgreSQL / TimescaleDB DDL)

Team BE có thể copy trực tiếp đoạn mã SQL này để tạo bảng trên cơ sở dữ liệu Cloud của họ:

```sql
-- 1. Bảng lưu trữ các Lô dữ liệu (Batches)
CREATE TABLE IF NOT EXISTS cems_telemetry_batches (
    batch_id VARCHAR(64) PRIMARY KEY,
    gateway_id VARCHAR(64) NOT NULL,
    schema_version VARCHAR(16) NOT NULL DEFAULT '1.2.3',
    batch_created_at TIMESTAMPTZ NOT NULL,
    readings_count INTEGER NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    ack_token VARCHAR(128) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Bảng lưu trữ chuỗi thời gian các Bản ghi phút (Minute Readings)
CREATE TABLE IF NOT EXISTS cems_minute_readings (
    id BIGSERIAL PRIMARY KEY,
    batch_id VARCHAR(64) REFERENCES cems_telemetry_batches(batch_id) ON DELETE CASCADE,
    stack_id VARCHAR(64) NOT NULL,
    device_id VARCHAR(64) NOT NULL,
    measurement_config_id VARCHAR(64) NOT NULL,
    minute_ts TIMESTAMPTZ NOT NULL,
    window_seconds INTEGER NOT NULL DEFAULT 60,
    gap_reason VARCHAR(64),
    
    -- Số lượng mẫu
    expected_samples INTEGER NOT NULL DEFAULT 6,
    received_samples INTEGER NOT NULL DEFAULT 0,
    
    -- Nồng độ khí & Bụi (% và ppm)
    co2_pct_avg DECIMAL(6,2),
    so2_ppm_avg DECIMAL(8,2),
    nox_ppm_avg DECIMAL(8,2),
    co_ppm_avg DECIMAL(8,2),
    o2_pct_avg DECIMAL(6,2),
    pm_mg_m3_avg DECIMAL(8,2),
    
    -- Vật lý khí thải ống khói
    flue_flow_m3_h_avg DECIMAL(10,2),
    flue_temp_c_avg DECIMAL(6,2),
    pressure_gauge_kpa_avg DECIMAL(6,2),
    pressure_abs_kpa_avg DECIMAL(8,3),
    
    -- Môi trường phòng đặt trạm (DHT11)
    ambient_temp_c_avg DECIMAL(5,2),
    ambient_humidity_pct_avg DECIMAL(5,2),
    
    -- Tích phân thể tích khí chuẩn (Dùng tính Tín chỉ Carbon)
    co2_standard_volume_m3 DECIMAL(12,4) NOT NULL,
    flue_gas_standard_volume_m3 DECIMAL(14,4) NOT NULL,
    
    -- Khối lượng CO2 tính toán (Backend tự nhân: co2_m3 * 1.977)
    co2_mass_kg DECIMAL(12,4) GENERATED ALWAYS AS (co2_standard_volume_m3 * 1.977) STORED,
    co2_mass_ton_tco2e DECIMAL(14,6) GENERATED ALWAYS AS ((co2_standard_volume_m3 * 1.977) / 1000.0) STORED,
    
    -- Kiểm soát chất lượng QA/QC
    qaqc_status VARCHAR(32) NOT NULL DEFAULT 'ok',
    is_calibrating BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Ràng buộc chống trùng lặp theo ống khói và mốc thời gian
    CONSTRAINT uq_stack_minute UNIQUE (stack_id, minute_ts)
);

-- Tạo Index để tối ưu truy vấn chuỗi thời gian cho Dashboard & Báo cáo
CREATE INDEX IF NOT EXISTS idx_cems_minute_ts ON cems_minute_readings (stack_id, minute_ts DESC);
```

---

### 7. Danh Mục Bàn Giao Cho Team BE

1. **Tài liệu Đặc tả Kỹ thuật chuẩn**: [specs/2026-09-18-cems-iot-backend-integration-spec.md](file:///Users/macbook/Documents/Pione/carbon-iot-huong1/docs/superpowers/specs/2026-09-18-cems-iot-backend-integration-spec.md).
2. **Server Mẫu (Reference Implementation)**: [gateway/server.py](file:///Users/macbook/Documents/Pione/carbon-iot-huong1/gateway/server.py) (Team BE có thể chạy làm mẫu chuẩn).