# TỔNG QUAN — Hướng 1: IoT đo phát thải CO2 (Bên phát thải)

## 1. Mục tiêu bài toán

Đo lượng CO2 một nhà máy/doanh nghiệp thải ra thực tế, làm căn cứ xác định thiếu/dư allowance — từ đó biết cần mua tín chỉ carbon bù đắp bao nhiêu.

**IoT chỉ chịu trách nhiệm đến đâu**: thu thập, làm sạch, và gửi dữ liệu thô (Actual Emissions) lên hệ thống. Việc tính Baseline, áp phương pháp luận, so sánh Baseline vs Actual để ra kết quả cuối — **thuộc về Backend/Cloud, không phải IoT**.

---

## 2. Phương pháp đo — đã chốt

Có 2 phương pháp tồn tại trên thị trường, đã cân nhắc và chọn 1:

| Phương pháp | Cách đo | Đã chọn? |
|---|---|---|
| CEMS trọn gói (extractive) | Hút mẫu khí ra ngoài, xử lý rồi phân tích — chính xác cao, đạt kiểm định pháp lý, nhưng chi phí trăm triệu–vài tỷ đồng/trạm | Không — không khả thi để nhân rộng nhiều khách hàng |
| **CEMS in-situ đơn giản hóa** | Gắn cảm biến trực tiếp vào ống khói, không lấy mẫu ra ngoài | **Đã chọn trong phân tích nội bộ — chưa được lead duyệt chính thức.** Lý do chọn: chi phí thấp hơn, dễ nhân rộng, chấp nhận đánh đổi chưa đạt chuẩn kiểm định pháp lý (MCERT/TUV) ở giai đoạn đầu |

---

## 3. Đại lượng cần đo — Core measurement

| Đại lượng | Vai trò | Nguyên lý đo |
|---|---|---|
| Nồng độ CO2 (%) | Input chính | NDIR — CO2 hấp thụ ánh sáng hồng ngoại ở bước sóng riêng, không lẫn khí khác |
| Lưu lượng khí thải (m³/giờ) | Input chính | Siêu âm hoặc chênh áp suất |
| Nhiệt độ khí thải | Quy đổi về điều kiện chuẩn | Thermocouple |
| Áp suất khí thải | Quy đổi về điều kiện chuẩn | Pressure sensor |

**Lưu ý phạm vi**: công thức tính khối lượng CO2 từ các đại lượng trên thuộc về Backend, không phải IoT — tài liệu này chỉ mô tả IoT cần đo gì, không mô tả cách tính toán sau đó.

---

## 4. Kiến trúc hệ thống — 3 lớp (đã xác nhận giữ nguyên)

```
[Cảm biến gắn trực tiếp ống khói]
  CO2 (NDIR) | Lưu lượng | Nhiệt độ | Áp suất
        |
        | RS485 / 4-20mA
        v
[Node / Edge Gateway — đọc cảm biến, xử lý sơ bộ, đóng gói]
        |
        | LoRa / 4G / WiFi / LAN nội bộ nhà máy (tùy khoảng cách thực tế)
        v
[Gateway trung tâm — gom dữ liệu từ 1 hoặc nhiều Node]
        |
        | 4G / WiFi / LAN
        v
[Cloud / Backend]
        |
        | raw data đã làm sạch + metadata
        v
[Backend — NGOÀI PHẠM VI IOT]
  Nhúng phương pháp luận → Tính Baseline vs Actual → Khối lượng CO2 → Verify → Credit/Allowance
```

**Lý do giữ 3 lớp thay vì rút gọn còn 2**: dự phòng trường hợp nhà máy có nhiều ống khói/nhiều điểm đo phân tán — khi đó nhiều Node có thể gom chung về 1 Gateway trước khi ra Cloud, tránh mỗi Node phải tự trang bị kết nối 4G riêng (tốn chi phí).

**Lưu ý về số lượng Node thực tế**: nếu Pilot chỉ có 1 ống khói, có thể chỉ cần 1 Node duy nhất (đọc gộp cả 4 cảm biến CO2/lưu lượng/nhiệt độ/áp suất) — vẫn đúng kiến trúc 3 lớp, chỉ là Gateway lúc này gom dữ liệu từ 1 Node thay vì nhiều Node.

---

## 5. Bổ sung kỹ thuật — để "thực tế hơn" (theo ghi chú diễn đàn Carbon 2026)

Đối chiếu 5 ràng buộc kỹ thuật thực tế từ diễn đàn, áp dụng cho Hướng 1:

| Ràng buộc | Áp dụng | Mức ưu tiên cho Pilot |
|---|---|---|
| **Lọc nhiễu (Edge Preprocessing)** | Nhà máy có động cơ công suất lớn tạo nhiễu điện từ (EMI) mạnh khi khởi động — ảnh hưởng trực tiếp độ chính xác cảm biến CO2/lưu lượng. Cần lọc nhiễu (ví dụ Moving Average) tại Node trước khi gửi | **Cao — nên làm ngay** |
| **Store-and-Forward** | Nhà xưởng kết cấu tôn có thể chắn sóng như lồng Faraday, gây mất kết nối tạm thời. Cần cơ chế lưu tạm dữ liệu (buffer/file local) khi mất mạng, gửi lại khi phục hồi | **Cao — nên làm ngay**, đặc biệt vì Hướng 1 vận hành trong nhà xưởng (khác rừng mở của Hướng 2) |
| **Watchdog Timer** | Hệ thống CEMS cần chạy 24/7/365, không có người can thiệp trực tiếp tại ống khói. Cần cơ chế tự khởi động lại khi firmware bị treo | Trung bình — ghi nhận là hiểu biết, có thể chưa code cho Pilot v1 |
| **Hardware Security** (Secure Boot, chip bảo mật riêng, khóa cổng nạp code) | Bảo vệ thiết bị khỏi bị can thiệp/giả mạo dữ liệu | Thấp cho Pilot — thuộc mức production, là định hướng dài hạn |
| **Ranh giới Edge vs Backend** | Node/Edge Gateway chỉ tính Actual Emissions (đã lọc nhiễu, làm sạch) — **không biết và không cần biết Baseline là gì**. Baseline và phương pháp luận chỉ được nhúng ở Backend/Cloud | Đã thống nhất — áp dụng làm nguyên tắc thiết kế, không phải việc cần code thêm |

---

*Tài liệu này mô tả tổng quan hệ thống Hướng 1 — mục tiêu, phương pháp đo, kiến trúc, và các ràng buộc kỹ thuật cần lưu ý. Không phải kế hoạch triển khai hay Proposal chính thức trình lead.*
