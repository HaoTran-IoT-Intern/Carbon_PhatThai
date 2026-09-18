# PLAN — Bổ sung Firmware CEMS (Hướng 1, v2)

## Mục tiêu

Mở rộng firmware Proof of Concept đã có (4 thông số: CO2, lưu lượng, nhiệt độ, áp suất) thành bản mô phỏng sát với CEMS hoàn chỉnh hơn — theo đúng `tong_quan_huong1_v2.md`, trong lúc chờ lead xác nhận Hướng A/B.

## Phạm vi Plan này

Chỉ mở rộng firmware/Gateway đã có. Không mua thiết bị thật, không viết Adapter đọc Modbus thật (chỉ giả lập cấu trúc code cho việc đó).

---

## Đối chiếu — đã có gì, cần thêm gì

| Thành phần | Đã có (bản v1) | Cần thêm (bản v2) |
|---|---|---|
| Khí đo | CO2 | + SO2, NOx, CO, O2 (theo xác nhận) |
| Bụi | Chưa có | + PM |
| Vật lý đi kèm | Nhiệt độ, áp suất, lưu lượng | Giữ nguyên — đã đúng, xác nhận là bắt buộc |
| QA/QC | Chưa có | Mock zero/span drift, status in_control/out_of_control |
| Protocol layer | Chưa có (gửi thẳng MQTT) | Thêm hàm giả lập đọc Modbus register (cấu trúc code, chưa cần chính xác) |
| Lọc nhiễu (MA-5) | Có | Áp dụng cho các trường mới |
| Store-and-Forward | Có | Không đổi |

---

## Danh sách việc — theo thứ tự

| # | Việc | Input cần có trước | Output |
|---|---|---|---|
| 1 | Thêm 5 hàm mock mới: `read_so2()`, `read_nox()`, `read_co()`, `read_o2()`, `read_pm()` — mỗi hàm biên độ dao động riêng theo đặc tính từng khí (ví dụ O2 dao động hẹp quanh 15-21%, NOx dao động rộng hơn) | Firmware v1 hiện có | 5 hàm đọc mock, tích hợp vào buffer sampling đã có |
| 2 | Mở rộng struct/JSON payload thêm các trường mới: `so2_ppm`, `nox_ppm`, `co_ppm`, `o2_pct`, `pm_mgm3` | Việc 1 hoàn thành | JSON payload đầy đủ 6 khí+bụi + 3 vật lý |
| 3 | Viết hàm mock QA/QC: `mock_zero_drift()`, `mock_span_drift()`, `qaqc_status()` — trả về "in_control"/"out_of_control" dựa ngưỡng 2% | Việc 2 hoàn thành | Khối `qaqc` trong JSON, có logic ngưỡng thật (không chỉ random cố định) |
| 4 | Thêm 2 field tĩnh (không đổi theo runtime): `last_rata_date`, `rata_accuracy_pct` — mock giá trị cố định | Việc 3 hoàn thành | JSON có đủ thông tin RATA tham chiếu |
| 5 | Viết hàm giả lập Protocol layer: `read_modbus_register(uint8_t regAddr)` — hiện trả mock, nhưng đặt đúng vị trí trong luồng đọc dữ liệu, để sau này thay bằng thư viện Modbus thật mà không cần sửa cấu trúc | Việc 1-4 hoàn thành | Cấu trúc code phản ánh đúng vai trò Adapter, sẵn sàng thay thế khi có CEMS thật |
| 6 | Cập nhật `gateway.py`: parse đầy đủ trường mới, thêm cột tương ứng trong SQLite (`so2_ppm`, `nox_ppm`, `co_ppm`, `o2_pct`, `pm_mgm3`, `zero_drift_pct`, `span_drift_pct`, `qaqc_status`) | Việc 2-4 hoàn thành | SQLite schema đầy đủ, lưu được toàn bộ dữ liệu mới |
| 7 | Thêm logic Gateway: khi nhận bản tin có `qaqc_status == "out_of_control"`, vẫn lưu nhưng gắn cờ riêng (ví dụ in cảnh báo `[QA/QC WARNING]`, không tính vào thống kê trung bình nếu có truy vấn tổng hợp) | Việc 6 hoàn thành | Gateway phân biệt được dữ liệu tin cậy và không tin cậy |
| 8 | Test: chủ động ép `mock_span_drift()` vượt ngưỡng 2% liên tục (qua biến test hoặc random có xu hướng), xác nhận Gateway phát hiện đúng và gắn cờ `out_of_control` | Việc 7 hoàn thành | Bằng chứng cơ chế QA/QC hoạt động đúng |

---

## Điều kiện hoàn thành (Definition of Done)

- Firmware publish đầy đủ 6 khí/bụi (CO2, SO2, NOx, CO, O2, PM) + 3 vật lý (nhiệt độ, áp suất, lưu lượng) + khối QA/QC.
- Có hàm giả lập Protocol layer, đặt đúng vị trí trong luồng đọc dữ liệu.
- Gateway lưu đầy đủ, phân biệt được dữ liệu `in_control` và `out_of_control`.
- Demo được: ép drift vượt ngưỡng → Gateway phát hiện và gắn cờ đúng.

## Không thuộc phạm vi Plan này

- Đọc Modbus thật (cần cảm biến/CEMS thật, thư viện `ModbusMaster`) — chỉ giả lập cấu trúc, chưa nối phần cứng thật.
- NH3, HCl — theo xác nhận, tạm không đưa vào bản mock này.
- Adapter pattern đa vendor — chỉ ghi nhận nguyên tắc thiết kế, chưa code cho nhiều loại CEMS khác nhau.
- Quyết định Hướng A hay B — đang chờ lead xác nhận, Plan này chỉ chuẩn bị kỹ thuật dùng được cho cả 2 hướng.
