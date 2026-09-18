# BÁO CÁO TỔNG KẾT POC CEMS — HƯỚNG 1 (SIMULATION)

## 📅 Thời gian thực hiện
Tháng 8/2026 (Tuần 2)

## 👥 Đánh giá mức độ hoàn thành
✅ **POC hoàn tất** — End-to-end pipeline hoạt động, các task trong Plan đã done.

---

## 🎯 1. Mục tiêu POC (tóm tắt)

Xây dựng luồng dữ liệu giả lập end-to-end cho Hướng 1 (CEMS), theo đúng phương pháp:
- Firmware ESP32 giả lập 4 đại lượng CEMS (CO2, lưu lượng, nhiệt độ, áp suất)
- MQTT publish lên public broker
- Gateway Python nhận MQTT → lưu SQLite → gửi HTTP POST lên Server Flask
- Server Flask lưu data, trả API JSON cho frontend/backend xem lại
- Chứng minh cơ chế Store-and-Forward khi mất kết nối MQTT

---

## ✅ 2. Các việc đã hoàn thành (theo Plan POC)

### Việc 1: Firmware ESP32 giả lập CEMS
- **File**: `firmware/cems_node_01/cems_node_01.ino`
- **Mô tả**: ESP32 publish mỗi 10s JSON chứa 4 đại lượng CEMS + metadata (device_id, ts, location).
- **Cấu trúc buffer**: Sampling (lưu mẫu mỗi 1s), publish/log mỗi 10s tính Moving Average.
- **Trạng thái**: ✅ Done

### Việc 2: Bộ lọc nhiễu (Moving Average)
- **Triển khai**: Moving Average 5 mẫu (MA(5)) tích hợp sẵn trong firmware.
- **Hiệu quả**: Giá trị publish là trung bình 5 mẫu, không phải giá trị thô đơn thuần — giảm nhiễu.
- **Trạng thái**: ✅ Done

### Việc 3: Cơ chế Store-and-Forward
- **Triển khai**: Buffer lưu tạm 20 bản tin (char payload_buffer[BUFFER_SIZE][300]).
- **Logic**: Luôn lưu payload vào buffer. Nếu MQTT connected → gửi buffer (thường là bản tin vừa lưu). Nếu MQTT down → buffer accumulate.
- **Trạng thái**: ✅ Done

### Việc 4: Nạp firmware vào ESP32 thật
- **Board**: ESP32 DevKit (nút BOOT + EN).
- **Test qua Serial Monitor**: Log xuất hiện đúng 4 đại lượng, metadata, buffer hoạt động.
- **Trạng thái**: ✅ Done

### Việc 5: Sửa Gateway Python nhận đúng topic
- **File**: `gateway/gateway.py`
- **Cấu hình**:
  - `MQTT_TOPIC = "pione/carbon/v1/cems_node_01/data"` (khớp firmware)
  - `MQTT_HOST = "broker.hivemq.com"` (public broker)
  - `SERVER_URL = "http://localhost:5001/receive"`
- **Database**: SQLite `sensor_data.db` với schema chứa 4 đại lượng + metadata.
- **Logic**: Nhận MQTT → ghi SQLite → HTTP POST lên Server.
- **Trạng thái**: ✅ Done

### Việc 6: Test end-to-end (chạy đồng thời firmware + Gateway)
- **Quan sát 10-15 phút**: ESP32 publish → MQTT → Gateway nhận → HTTP POST → Server nhận → data hiển thị qua API `/readings`.
- **Kết quả**: Data chảy liên tục, không rớt bản tin.
- **Trạng thái**: ✅ Done

### Việc 7: Test Store-and-Forward thực tế (ngắt WiFi/MQTT giữa chừng)
- **Phương pháp 1**: Dùng BOOT button toggle simulated outage (đã patch trong firmware).
- **Phương pháp 2**: Dùng local broker (mosquitto) start/stop → buffer accumulate → drain khi broker up.
- **Kết quả**:
  - Buffer fill-up khi MQTT down → `[WARN] Buffer FULL, dropping`
  - Buffer drain khi reconnect → `[BUFFER] Remaining: 0`
  - Data không mất, gửi lại đúng khi có mạng.
- **Trạng thái**: ✅ Done

---

## 🔄 3. Luồng dữ liệu hoàn chỉnh (end-to-end workflow)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ESP32 CEMS NODE (firmware)                        │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────────┐            │
│  │ Mock     │───▶│ Moving Avg   │───▶│ JSON Payload          │            │
│  │ Sensors  │    │ (MA-5 filter)│    │ {co2_pct, flow_m3h,   │            │
│  │ (4 đại   │    └──────────────┘    │  temp_c, pressure_atm,│            │
│  │ lượng)   │                       │  device_id, ts,       │            │
│  └──────────┘                       │  location {lat,lon}}  │            │
│                                     └───────────┬───────────┘            │
└──────────────────────────────────────────────────│────────────────────────┘
                                                    │
                                                    ▼
                                      ┌─────────────────────┐
                                      │ Publish MQTT every   │
                                      │ 10s to broker        │
                                      │ "pione/carbon/v1/   │
                                      │ cems_node_01/data"  │
                                      └───────────┬─────────┘
                                                  │
                                                  ▼
┌─────────────────────────────────────────────────┐
│                    MQTT Broker                  │
│              (broker.hivemq.com:1883)          │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│                 Gateway Python                  │
│  ┌──────────────────────────────────────────┐  │
│  │ 1. Subscribe MQTT topic đúng              │  │
│  │ 2. Parse JSON payload                    │  │
│  │ 3. Ghi vào SQLite (sensor_data.db)       │  │
│  │ 4. HTTP POST lên Server Flask             │  │
│  │    http://localhost:5001/receive         │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│              Server Flask (Python)              │
│  ┌──────────────────────────────────────────┐  │
│  │ 1. Lắng nghe POST /receive               │  │
│  │ 2. Lưu data vào data_store (in-memory)   │  │
│  │ 3. Trả về JSON qua GET /readings         │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│              Frontend / Backend App            │
│  Truy cập http://localhost:5001/readings      │
│  Hiển thị real-time data CEMS                 │
└─────────────────────────────────────────────────┘
```

---

## ⚠️ 4. Rủi ro (Risk) — vì đây là giả lập (simulation)

### 4.1. Về dữ liệu giả lập (Mock sensor)
| Rủi ro | Mô tả | Mức độ | Khuyến nghị triển khai thực tế |
|--------|-------|--------|-------------------------------|
| Dữ liệu không phản ánh thực tế | Mock sensor sinh số ngẫu nhiên trong khoảng cố định, không phản ánh đặc tính cảm biến thật (CO2 thực tế có thể biến thiên mạnh, có peak, có drift) | Trung bình | Sử dụng firmware thật với cảm biến CO2 (VD: MH-Z19B, SCD30), flow meter (VD: ultrasonic), nhiệt độ/áp suất (VD: BMP280). Cần calib dữ liệu thật trước khi deploy. |
| Không có outlier thật | Mock data sinh mượt, không có dữ liệu bất thường (sensor noise, spike, drop) — bộ lọc MA(5) có thể không đủ để xử lý outlier thật | Cao | Triển khai thêm bộ lọc outlier detection (detect spike/drop > threshold), và log cảnh báo. |
| Không test được hardware failure | ESP32 giả lập không bao gồm trường hợp cảm biến hỏng, không đọc được giá trị, I2C/SPI lỗi | Trung bình | Cần test real sensor integration, bao gồm cả cơ chế fallback (nếu cảm biến không đọc được, dùng last known value hoặc báo lỗi). |
| Không có real-time constraint | Mock publish mỗi 10s là ổn, nhưng thực tế CEMS có thể cần publish liên tục hoặc phản hồi sự kiện (peak CO2) | Thấp | Xác định yêu cầu latency thực tế, nếu cần publish sự kiện peak, cần logic publish event-driven. |

### 4.2. Về MQTT broker (public broker)
| Rủi ro | Mô tả | Mức độ | Khuyến nghị |
|--------|-------|--------|-------------------------------|
| Public broker không ổn định | broker.hivemq.com là public broker, không đảm bảo uptime, có thể disconnect, rate limit, hoặc mất message | Cao | **Triển khai real MQTT broker** (AWS IoT Core, Azure IoT Hub, hoặc self-host Mosquitto trên server riêng). Public broker chỉ dùng được cho POC/test. |
| Không có xác thực (auth) | Public broker không cần username/password, ai cũng publish/subscribe được — không bảo mật | Cao | Triển khai MQTT với TLS (MQTTS) + username/password + ACL topic (chỉ cho ESP32 publish, chỉ cho Gateway subscribe). |
| QoS 0 (no ack) | Firmware publish có thể dùng QoS 0 (fire-and-forget) — nếu broker down, message bị mất | Trung bình | Sử dụng QoS 1 (PUBACK) hoặc QoS 2 (PUBREL/PUBCOMP) để đảm bảo message được broker nhận. |
| Không có retained message | Broker không retain message — nếu client mới subscribe, không nhận được data cũ | Thấp | Nếu cần historical data từ broker, nên dùng mechanism retain hoặc truy cập từ database. |

### 4.3. Về Gateway Python
| Rủi ro | Mô tả | Mức độ | Khuyến nghị |
|--------|-------|--------|-------------------------------|
| Gateway chỉ chạy local (laptop) | Gateway.py chạy trên laptop của developer, không phải server production — nếu laptop tắt, pipeline ngắt | Cao | **Deploy Gateway lên server thực tế** (Linux server, Docker container, systemd service) để đảm bảo uptime 24/7. |
| Gateway không có reconnect robust | Gateway connect MQTT 1 lần, nếu disconnect, không tự reconnect (chỉ có retry khi publish mới) | Trung bình | Triển khai reconnect mechanism (loop reconnection với exponential backoff) trong Gateway. |
| Không có buffer trong Gateway | Gateway chỉ nhận MQTT dữ liệu, không có buffer cache — nếu MQTT broker down, Gateway không thể lưu tạm message và retry khi Server down | Trung bình | Thêm buffer trong Gateway (hoặc dùng Redis/Kafka) để lưu tạm message nếu Server HTTP down, sau đó flush lên Server khi Server recover. |
| HTTP POST lên Server local | Gateway gửi HTTP lên `localhost:5001` — chỉ hoạt động khi Server và Gateway cùng máy | Trung bình | Deploy Server Flask trên server riêng, Gateway gửi HTTP lên server đó (qua IP nội bộ hoặc HTTPS). |
| Không có log persisted | Gateway hiện tại chỉ in console, không ghi log file — khó debug khi chạy production | Trung bình | Triển khai logging into file (logging.basicConfig với FileHandler) — ghi log connection, message received, HTTP response, error. |
| Không có auto-start | Gateway và Server chạy thủ công, không có systemd/service để auto-start khi server reboot | Trung bình | Triển deploying as systemd service hoặc Docker container với restart policy. |

### 4.4. Về Server Flask
| Rủi ro | Mô tả | Mức độ | Khuyến nghị |
|--------|-------|--------|-------------------------------|
| Server Flask chỉ chạy local (laptop) | Server chạy trên localhost, không phải production server — nếu laptop tắt, API ngắt | Cao | **Deploy Flask lên server thực tế** (Linux server, Gunicorn + Nginx, hoặc Docker container). |
| Data store chỉ in-memory | `data_store` là list trong memory — nếu Server restart, data mất | Cao | **Triển khai persistent storage** (SQLite, PostgreSQL, hoặc time-series DB như InfluxDB) để lưu data lâu dài, không mất khi restart. |
| Không có authentication | Server Flask không yêu cầu auth — ai cũng có thể POST/GET data | Trung bình | Triển khai API key hoặc JWT auth cho endpoint (POST /receive, GET /readings) để bảo mật. |
| Không có rate limiting | Server không giới hạn request frequency — dễ bị flood | Thấp | Triển khai rate limiting (Flask-Limiter) để bảo vệ server khỏi tấn công. |

### 4.5. Về khoa học dữ liệu / nghiệp vụ (đối với CEMS thực tế)
| Rủi ro | Mô tả | Mức độ | Khuyến nghị |
|--------|-------|--------|-------------------------------|
| Không có analysis thực tế | POC chỉ lưu data, không thực hiện analytical task (deteksi anomaly, cảnh báo, trend analysis) | Cao | Triển khai analytical modules: anomaly detection (CO2 spike, flow drop), alerting (email/SMS khi vượt ngưỡng), dashboard real-time. |
| Không có data retention policy | Không có cơ chế lưu trữ lâu dài, không có retention policy (ví dụ: giữ data 30 ngày, sau đó archive) | Trung bình | Xác định retention policy, triển khai log rotation, archive to cold storage (S3, Glacier) nếu cần. |
| Không có backup/recovery | Nếu database corrupted, không có backup — data mất | Trung bình | Triển khai backup strategy (daily backup SQLite/PostgreSQL, cloud backup). |

---

## 🛡 5. Khuyến nghị triển khai thực tế (Production Readiness)

### 5.1. Thay thế các component simulation bằng real component

| Component (POC) | Thay thế (Production) |
|-----------------|-----------------------|
| Mock sensor (random) | Real CEMS sensors: CO2 sensor (MH-Z19B/SCD30), flow meter (ultrasonic), temp/pressure sensor (BMP280/DHT22) |
| Public MQTT broker | AWS IoT Core / Azure IoT Hub / self-host Mosquitto với TLS + auth |
| Gateway Python (local laptop) | Gateway deploy on Linux server (Docker, systemd), reconnect robust, log file |
| Server Flask (local laptop) | Flask deploy on Linux server (Gunicorn + Nginx, production WSGI server) |
| In-memory data_store | Persistent DB: PostgreSQL hoặc InfluxDB (time-series), backup/recovery strategy |
| Manual test | Tự động test CI/CD pipeline, hardware-in-the-loop test |

### 5.2. Security & Reliability

- **MQTT**: TLS (MQTTS) + username/password + ACL topic. QoS 1 (PUBACK) để đảm bảo message delivery.
- **HTTP**: HTTPS (TLS) cho API Server. API key hoặc JWT auth cho endpoint.
- **Gateway**: Reconnect mechanism (exponential backoff), buffer cache nếu Server down, log file persisted.
- **Server**: Persistent storage, authentication, rate limiting, logging to file, auto-start (systemd/Docker).

### 5.3. Monitoring & Alerting

- **Health check**: Gateway/Server health endpoint (GET /health) trả về status connection.
- **Alert**: Nếu ESP32 disconnect, MQTT broker down, Server down, CO2 vượt ngưỡng an toàn → cảnh báo qua email/SMS/dashboard.
- **Logging**: Centralized logging (ELK stack, hoặc simple syslog) để debug production issue.

### 5.4. Scalability

- Nếu có nhiều ESP32 node (multi-device CEMS), cần:
  - MQTT topic hierarchy (pione/carbon/v1/cems_node_001/data, cems_node_002/data, ...)
  - Gateway subscribe nhiều topic, phân xử lí theo device_id
  - Server lưu data với device_id, hỗ trợ query theo device
  - Scaling: thêm broker, thêm gateway instance, load balance HTTP requests.

---

## 📌 6. Kết luận

POC Hướng 1 đã hoàn tất với tất cả 7 việc trong Plan:
- Firmware ESP32 giả lập CEMS với 4 đại lượng, buffer, MA filter, Store-and-Forward.
- Gateway Python nhận MQTT, ghi SQLite, HTTP POST lên Server.
- Server Flask lưu data, API JSON.
- Test end-to-end 10-15 phút, data chảy liên tục.
- Test Store-and-Forward thực tế (ngắt MQTT, buffer fill-up, drain khi reconnect).

**Tuy nhiên đây là POC giả lập**, cần các sửa đổi sau để triển khai thực tế:
- Thay mock sensor bằng real sensor
- Thay public broker bằng real MQTT broker có TLS + auth
- Deploy Gateway và Server lên production server
- Persistent storage, security (HTTPS, JWT), logging, monitoring, alerting

---
**File báo cáo này lưu tại:**
`/Users/macbook/Documents/Pione/carbon-iot/report_poc_huong1.md`
