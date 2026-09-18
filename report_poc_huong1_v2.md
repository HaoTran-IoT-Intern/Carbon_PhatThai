# BÁO CÁO TỔNG KẾT POC CEMS — HƯỚNG 1 (V2 - MỞ RỘNG)

## Thời gian thực hiện
Tháng 8/2026 (Tuần 3)

## Đánh giá mức độ hoàn thành
**POC v2 hoàn tất** — End-to-end pipeline mở rộng hoạt động hoàn chỉnh, đáp ứng tất cả các task nâng cao trong Plan v2.

---

## 1. Mục tiêu POC v2 (tóm tắt)

Nâng cấp và mở rộng hệ thống giả lập CEMS Hướng 1 từ phiên bản v1 cơ bản lên phiên bản v2 công nghiệp:
- **Mở rộng cảm biến**: Từ 4 thông số lên 9 thông số (6 khí + bụi: CO2, SO2, NOx, CO, O2, PM; và 3 thông số vật lý: lưu lượng, nhiệt độ, áp suất).
- **Tích hợp Quy trình QA/QC**: Mô phỏng sai số zero/span drift, tự động đánh giá trạng thái `in_control` / `out_of_control` theo ngưỡng tiêu chuẩn 2%.
- **Tích hợp Quy trình RATA tĩnh**: Lưu trữ metadata kiểm định RATA cố định (`last_rata_date`, `rata_accuracy_pct`).
- **Lớp Giao thức Modbus (Protocol Layer)**: Xây dựng module `modbus_adapter.py` giả lập giao thức Modbus TCP/RTU song song với truyền thông MQTT.
- **Mở rộng Gateway & Server**: Tự động migrate cơ sở dữ liệu SQLite, cập nhật parser xử lý đủ 14 trường dữ liệu, hiển thị cảnh báo QA/QC và lưu trữ hoàn chỉnh tại Server Flask.

---

## 2. Các việc đã hoàn thành (theo Plan POC v2)

### Việc 0: Khắc phục lỗi Buffer FULL & Tối ưu hóa MQTT Packet
- **Sự cố v1**: PubSubClient giới hạn gói tin mặc định `256 bytes`. Khi mở rộng lên 9 cảm biến, payload JSON vượt quá 256 bytes khiến hàm `publish()` bị từ chối âm thầm, làm đầy buffer 20 bản tin sau 200s.
- **Giải pháp**: Đã tăng cấu hình `#define MQTT_MAX_PACKET_SIZE 2048` và `MAX_PAYLOAD_LEN 1500` ngay trước khi khai báo thư viện PubSubClient trong firmware.
- **Trạng thái**: -> Done

### Việc 1 & 2: Mở rộng 9 Đại lượng & Moving Average (MA(5))
- **File**: `firmware/cems_node_01/cems_node_01.ino`
- **Mock cảm biến**: Đã viết thêm 5 hàm mock: `mock_so2()`, `mock_nox()`, `mock_co()`, `mock_o2()`, `mock_pm()`.
- **Bộ lọc MA(5)**: Khai báo 9 mảng lịch sử riêng biệt. Hàm `get_filtered_data()` tính toán Trung bình động (Moving Average) 5 mẫu gần nhất cho cả 9 đại lượng trước khi đóng gói JSON, giảm thiểu độ nhiễu đột ngột.
- **Trạng thái**: -> Done

### Việc 3: Tích hợp Mock QA/QC (Drift Testing)
- **Cơ chế**: Sinh ngẫu nhiên sai số zero drift (khoảng ±2.0%) và span drift (khoảng ±3.0%) thông qua hàm `mock_zero_drift()` và `mock_span_drift()`.
- **Logic đánh giá**: Trạng thái `qaqc_status` tự động gán là `"in_control"` nếu cả hai trị tuyệt đối của sai số nhỏ hơn 2.0%, ngược lại gán là `"out_of_control"`.
- **Trạng thái**: -> Done

### Việc 4: Tích hợp RATA tĩnh
- **Metadata**: Lưu trữ thông số kiểm định RATA tĩnh gồm `last_rata_date` ("2026-08-20") và `rata_accuracy_pct` (1.5%) trực tiếp trong cấu trúc `SensorData`.
- **Trạng thái**: -> Done

### Việc 5: Lớp Giao thức (Modbus Adapter Mock)
- **File**: `gateway/modbus_adapter.py` và `gateway/gateway.py`
- **Mô tả**: Thiết lập lớp `MockModbusDevice` giả lập một thiết bị công nghiệp truyền dữ liệu qua Modbus TCP/RTU.
- **Chế độ Modbus**: Gateway hỗ trợ chạy độc lập qua tham số `--modbus` (`python3 gateway.py --modbus`). Ở chế độ này, Gateway sẽ polling dữ liệu từ Modbus adapter mỗi 10 giây, tự động ghi SQLite và HTTP forward lên server thay vì subscribe MQTT.
- **Trạng thái**: -> Done

### Việc 6 & 7: Nâng cấp Gateway & Tự động Migration DB
- **File**: `gateway/gateway.py`
- **Migration**: Hàm `migrate_db()` tự động kiểm tra cấu trúc bảng SQLite hiện tại và thêm 10 cột mới bằng lệnh `ALTER TABLE` mà không làm mất mát bất kỳ bản ghi lịch sử nào từ v1.
- **Parser**: Gateway parse đầy đủ 14 trường JSON từ ESP32 MQTT hoặc Modbus.
- **Cảnh báo QA/QC**: Thêm logic kiểm tra cờ `qaqc_status`. Nếu phát hiện `"out_of_control"`, hệ thống lập tức in cảnh báo `[WARN] QA/QC out_of_control!` kèm chi tiết chỉ số drift để kỹ sư vận hành xử lý.
- **Trạng thái**: -> Done

### Việc 8: Thử nghiệm Ép Drift vượt ngưỡng (Stress Test)
- **Phương pháp**: Patch tạm thời `mock_span_drift()` luôn trả về giá trị cố định `3.0%` (> 2.0%).
- **Kết quả**:
  - ESP32 gửi bản tin có `span_drift_pct: 3.00` và `qaqc_status: "out_of_control"`.
  - Gateway nhận dữ liệu và xuất cảnh báo chuẩn xác:
    `[WARN] QA/QC out_of_control! zero_drift=0.90% span_drift=3.00%`
  - Cơ chế cảnh báo QA/QC hoạt động hoàn hảo 100%. Sau đó, firmware đã được revert về chế độ random bình thường.
- **Trạng thái**: -> Done

---

## 3. Luồng dữ liệu hoàn chỉnh v2 (End-to-End Workflow)

```
                     ┌───────────────────────────────────────────────┐
                     │          ESP32 CEMS NODE (firmware)           │
                     │  - Mock 9 sensors (6 gases + PM, 3 physical)  │
                     │  - MA(5) Noise Filter                         │
                     │  - Mock QA/QC Drift & RATA Metadata           │
                     │  - MQTT Store-and-Forward (Max packet 2048)   │
                     └───────────────────────┬───────────────────────┘
                                             │ (Publish MQTT every 10s)
                                             ▼
 ┌────────────────────────┐        ┌───────────────────┐
 │ Mock Modbus Device     │        │    MQTT Broker    │
 │ (modbus_adapter.py)    │        │ (broker.hivemq.com│
 │ - Mock 14 fields       │        │     Port 1883)    │
 └───────────┬────────────┘        └─────────┬─────────┘
             │                               │
             │ (Poll every 10s               │ (Subscribe MQTT topic)
             │  if --modbus is set)          │
             ▼                               ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                              Gateway Python                                │
│  1. Receive data (MQTT subscribe OR Modbus adapter poll)                   │
│  2. Auto Migrate DB & Save 14 fields into SQLite (cems_data.db)            │
│  3. Detect QA/QC Out-of-Control -> Trigger [WARN] alert log in terminal   │
│  4. Forward complete JSON (14 fields) to Flask Server via HTTP POST        │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │
                                     ▼ (HTTP POST /receive)
┌────────────────────────────────────────────────────────────────────────────┐
│                            Server Flask (Python)                           │
│  1. Listen on http://localhost:5001/receive                                │
│  2. Store full v2 JSON dataset (14 fields) in memory                       │
│  3. Expose GET /readings API for real-time visualization                   │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Rủi ro (Risk) — Cập nhật phiên bản v2

### 4.1. Về lớp Giao thức Modbus (Adapter Mock)
| Rủi ro | Mô tả | Mức độ | Khuyến nghị triển khai thực tế |
|--------|-------|--------|-------------------------------|
| Giả lập Modbus chưa qua serial/TCP socket thật | Adapter hiện tại là class Python nội bộ, chưa thực sự truyền thông qua giao thức TCP Port 502 hay RTU Serial | Trung bình | Sử dụng thư viện `pymodbus` hoặc `pyModbusTCP` để biến class adapter thành một Modbus Slave/Server thực sự lắng nghe trên port 502, Gateway sẽ đóng vai trò Modbus Master/Client kết nối qua TCP socket. |
| Polling block | Polling block đồng bộ (`time.sleep`) có thể gây nghẽn luồng nếu kết nối bị timeout | Thấp | Chuyển sang lập trình bất đồng bộ (`asyncio` hoặc threading riêng biệt) để polling nhiều Modbus devices song song mà không block gateway chính. |

### 4.2. Về Quy trình đánh giá QA/QC & RATA
| Rủi ro | Mô tả | Mức độ | Khuyến nghị triển khai thực tế |
|--------|-------|--------|-------------------------------|
| Đánh giá QA/QC tĩnh tại Edge | Logic đánh giá hiện tại nằm trên ESP32/Gateway thô sơ, chưa cấu hình được các chu kỳ test zero/span tự động qua solenoid valve | Cao | Trong thực tế, hệ thống CEMS phải điều khiển van điện từ (Solenoid) để thổi khí chuẩn (Zero gas/Span gas) vào buồng đo theo chu kỳ (ví dụ 24 giờ một lần), sau đó ghi nhận giá trị đo được để tính toán phần trăm drift thực tế. |
| Metadata RATA bị fix cứng | `last_rata_date` và `accuracy` bị fix cứng trên firmware, không cập nhật động được sau mỗi kỳ kiểm định thực tế | Trung bình | Cần cung cấp API hoặc giao thức Modbus ghi (Write Holding Registers) để cập nhật ngày kiểm định RATA mới nhất và độ chính xác thực tế vào bộ nhớ Flash/EEPROM của thiết bị. |

---

## 5. Khuyến nghị triển khai thực tế (Production Readiness v2)

### 5.1. Chuyển đổi Modbus Giả lập sang Công nghiệp thật
- **Phần cứng**: ESP32 sử dụng module chuyển đổi **TTL to RS485 (MAX485)** để giao tiếp với Gateway qua giao thức Modbus RTU công nghiệp.
- **Phần mềm**:
  - Trên Node/ESP32: Sử dụng thư viện `ModbusRTUTemplate` hoặc `ModbusServer` để ghi dữ liệu sensor vào các thanh ghi Input/Holding Registers (ví dụ: CO2 ở thanh ghi 30001, SO2 ở 30002).
  - Trên Gateway: Sử dụng `pymodbus` để kết nối cổng RS485 (ví dụ `/dev/ttyUSB0` với baudrate 9600, 8N1) để đọc dữ liệu thô theo chu kỳ, sau đó scale giá trị (ví dụ chia 100 đối với số thực lưu dạng số nguyên) rồi mới xử lý.

### 5.2. Quản lý dữ liệu không tin cậy (QA/QC Flag)
- Khi SQLite hay PostgreSQL truy vấn báo cáo (trung bình 1 giờ, 24 giờ để gửi lên Sở Tài nguyên Môi trường): **Phải loại bỏ hoàn toàn** các bản ghi có `qaqc_status == "out_of_control"` ra khỏi công thức tính toán trung bình, nhưng vẫn phải lưu trữ chúng để phục vụ mục đích audit và chứng minh sự cố thiết bị.
- Cần có hệ thống cảnh báo SMS/Email tự động tích hợp (như Twilio, Telegram Bot API) gửi thẳng đến kỹ thuật viên khi phát hiện thiết bị bị trôi ngưỡng (`out_of_control`) quá 2 chu kỳ liên tiếp.

---

## 6. Kết luận

Sự nâng cấp từ v1 lên v2 đã giải quyết toàn bộ các yêu cầu khắt khe của một hệ thống quản lý dữ liệu quan trắc CEMS tiêu chuẩn:
1. Mở rộng trọn vẹn số lượng cảm biến lên **9 thông số** (đầy đủ các khí độc và bụi theo quy định).
2. Xây dựng thành công cơ chế **tự đánh giá chất lượng dữ liệu (QA/QC)** với cờ trạng thái tin cậy rõ ràng.
3. Bổ sung lớp **Protocol Adapter Modbus** giúp hệ thống linh hoạt, dễ dàng tích hợp với các PLC, SCADA hay thiết bị công nghiệp có sẵn tại nhà máy.

Hệ thống giả lập hoạt động vô cùng ổn định, dữ liệu chạy mượt mạc, cấu trúc DB tự động cập nhật thông minh. Đây là tiền đề vững chắc để triển khai các thiết bị phần cứng thật trong giai đoạn tiếp theo.

---
**File báo cáo này lưu tại:**
`/Users/macbook/Documents/Pione/carbon-iot/report_poc_huong1_v2.md`
