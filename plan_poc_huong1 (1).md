# PLAN — Proof of Concept Firmware CEMS (Hướng 1)

## Mục tiêu

Xây dựng luồng dữ liệu end-to-end giả lập cho Hướng 1 (CEMS), theo đúng phương pháp và công cụ đã dùng thành công ở Hướng 2 — xác nhận logic hoạt động trước khi có phần cứng CEMS thật.

## Phạm vi Plan này

Chỉ áp dụng cho việc viết và test Proof of Concept (firmware giả lập + Gateway Python). Không bao gồm việc mua thiết bị thật, không bao gồm nội dung proposal.

---

## Danh sách việc — theo thứ tự thực hiện

| # | Việc | Input cần có trước | Output |
|---|---|---|---|
| 1 | Viết firmware `.ino` giả lập CEMS — 4 đại lượng: CO2, lưu lượng, nhiệt độ, áp suất. **Cấu trúc ngay từ đầu theo 2 chu kỳ tách biệt**: sampling (đọc mẫu, ví dụ mỗi 1s, lưu vào buffer) và logging/publish (mỗi 1-5 phút, tính trung bình buffer rồi publish 1 lần) — không viết kiểu đọc 1 lần rồi publish ngay, để Việc 2 không phải viết lại cấu trúc | Cấu trúc `.ino` mẫu đã có từ Hướng 2 (WiFi/MQTT/JSON) | File `.ino` chạy được, publish đúng chu kỳ logging, có buffer sample sẵn sàng cho Việc 2 |
| 2 | Thêm bộ lọc nhiễu (Moving Average / loại outlier) vào hàm tính trung bình buffer đã có sẵn từ Việc 1 | Việc 1 hoàn thành với đúng cấu trúc buffer | Dữ liệu publish là giá trị đã lọc, không phải trung bình thô đơn thuần |
| 3 | Thêm cơ chế Store-and-Forward (lưu tạm khi mất mạng) | Việc 1-2 hoàn thành | Firmware không mất dữ liệu khi ngắt kết nối tạm thời |
| 4 | Nạp firmware vào board ESP32 thật (đã có sẵn từ Hướng 2), test qua Serial Monitor | Việc 1-3 hoàn thành, board ESP32 sẵn có | Log `[TX]` hiển thị đúng 4 đại lượng, đúng metadata |
| 5 | Sửa Gateway Python (đã có từ Hướng 2) để nhận đúng topic Hướng 1 | Việc 4 hoàn thành | Gateway nhận và lưu đúng dữ liệu CEMS vào SQLite |
| 6 | Test end-to-end: chạy đồng thời firmware + Gateway, quan sát trong 10-15 phút | Việc 5 hoàn thành | Dữ liệu chảy liên tục, không rớt bản tin |
| 7 | Test cơ chế Store-and-Forward thực tế — ngắt WiFi giữa chừng, xem có mất dữ liệu không | Việc 6 hoàn thành | Xác nhận dữ liệu được lưu tạm và gửi lại đúng khi có mạng |

---

## Quy ước JSON — đã chốt, giữ nguyên

Tên field tự mô tả đơn vị, không cần field `unit` riêng — nhất quán với Hướng 2:

```json
{
  "device_id": "cems-node-001",
  "ts": "2026-08-13T10:00:00Z",
  "co2_pct": 8.2,
  "flow_m3h": 2010,
  "temp_c": 148,
  "pressure_atm": 1.18
}
```

## Điều kiện hoàn thành (Definition of Done)

- Firmware chạy ổn định, publish đúng 4 đại lượng CEMS + metadata, đúng chu kỳ.
- Có bộ lọc nhiễu hoạt động (giá trị publish khác giá trị đọc thô tức thời).
- Chứng minh được cơ chế Store-and-Forward hoạt động qua demo ngắt/nối mạng.
- Gateway Python nhận và lưu đầy đủ dữ liệu, có thể truy vấn lại qua SQLite Viewer.

## Không thuộc phạm vi Plan này

- Watchdog Timer, Hardware Security — chưa đưa vào Plan, theo đúng mức ưu tiên đã thống nhất trong tài liệu tổng quan.
- Viết Technical Design Document — làm sau khi Plan này hoàn thành.
