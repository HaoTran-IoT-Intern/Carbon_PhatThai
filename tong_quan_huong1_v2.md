# TỔNG QUAN HƯỚNG 1 — CEMS RESEARCH (Bản v2, thay thế bản cũ)

**Người thực hiện**: Hao — AI + IoT Intern
**Phạm vi**: Direction 1 — Hệ thống quan trắc khí thải liên tục (CEMS) cho nhà máy

## 1. Bối cảnh & Mục tiêu

Direction 1 cần cơ chế thu thập, xác thực và tích hợp dữ liệu phát thải từ nhà máy để phục vụ tính toán mức giảm phát thải và làm input cho hệ thống carbon credit.

**Lưu ý về scope**: Tài liệu nghiên cứu song song 2 hướng ứng dụng (compliance reporting và data-integration cho carbon credit), do bài toán nghiệp vụ cụ thể **chưa được xác nhận chính thức từ lead**. Phần lõi kỹ thuật (Mục 2-4) dùng chung cho cả hai hướng.

## 2. Các đại lượng đo & công nghệ cảm biến

### 2.1 CEMS đo những gì

**A. Thành phần khí và bụi** — phản ánh thành phần/nồng độ chất trong khí thải:
- Khí: CO2, CO, SO2, NOx, NH3, HCl, O2
- Bụi: PM/opacity

**B. Đại lượng vật lý đi kèm** — mô tả trạng thái dòng khí/hệ thống, **bắt buộc phải có** vì là điều kiện quy đổi cho mọi giá trị nồng độ khí:
- Nhiệt độ, áp suất, lưu lượng

### 2.2 Công nghệ đo — mỗi khí cần công nghệ riêng

| Công nghệ | Nguyên lý | Đo tốt | Ưu điểm | Nhược điểm |
|---|---|---|---|---|
| NDIR | Hấp thụ hồng ngoại | CO2, CO | Bền, ít bảo trì | Không đo tốt NOx, SO2 |
| UV-DOAS | Hấp thụ tử ngoại | SO2, NOx, NH3, HCl | Đo đa khí 1 thiết bị | Đắt hơn, nhạy bụi bám kính |
| Electrochemical | Phản ứng hóa học tại điện cực | O2 | Rẻ, nhỏ gọn | Tuổi thọ ngắn (1-2 năm) |
| PM (Opacity/Scatterometer) | Tán xạ ánh sáng qua bụi | Bụi (PM) | Đo liên tục | Sai số khi ống ẩm |

## 3. Kiến trúc CEMS tổng thể

```
LUỒNG CEMS GỐC: Ống khói → Probe → Heated Line (150-180°C) → SCS (Sample Conditioning)
                → Analyzer → DAHS → Output (Regulator/DCS/Carbon Platform)

LUỒNG TÍCH HỢP DỮ LIỆU (read-only, parallel tap):
Analyzer (tap A) / DAHS (tap B) → Integration Adapter → MQTT/JSON → Carbon Platform
```

### Vai trò từng khâu & điểm tích hợp ("tap point")

| Khâu | Chức năng | Có dữ liệu tích hợp? |
|---|---|---|
| Probe | Hút mẫu khí từ ống khói | Không — chỉ là ống vật lý |
| Heated Line | Giữ khí nóng, tránh ngưng tụ acid | Không |
| SCS | Lọc bụi, làm khô, tách ẩm | Có thể đọc nhiệt độ/áp suất/lưu lượng qua Modbus |
| **Analyzer** | Đo nồng độ từng khí | **Có — điểm tích hợp dễ nhất**: Modbus RTU hoặc 4-20mA |
| **DAHS** | Tính toán, hiệu chỉnh, lưu trữ, xuất báo cáo | **Có — điểm tích hợp giàu dữ liệu nhất**: database nội bộ, FTP, API/OPC-UA (nếu vendor mở) |
| Output | Gửi cơ quan quản lý/DCS/nền tảng carbon | Tùy kiến trúc, có thể parallel tap nếu được cấp quyền |

**Nhận định quan trọng**: Analyzer và DAHS là 2 điểm khả thi nhất để IoT tích hợp đọc dữ liệu mà không can thiệp luồng compliance gốc. Một số DAHS khóa API chỉ xuất cho cơ quan quản lý — cần làm việc với vendor xác nhận trước khi thiết kế chi tiết.

## 4. Protocol giao tiếp

| Protocol | Đặc điểm | Ứng dụng | Yêu cầu khi tích hợp |
|---|---|---|---|
| 4-20mA (Analog) | Biểu diễn giá trị bằng dòng điện, đơn giản, bền | CEMS thế hệ cũ | Gateway có analog input, hiệu chỉnh dải đo (4mA=0%, 20mA=100%) theo từng khí |
| Modbus RTU (RS-485) | Giao tiếp số, hỏi/đáp theo register, 1 dây đọc nhiều thông số | CEMS mới (2020+) | Biết register map — mỗi hãng định nghĩa khác nhau, cần xin tài liệu từ vendor |
| OPC-UA | Qua Ethernet, có encryption/authentication, kèm metadata | Nhà máy lớn dùng DCS | Cấu hình node tree, phức tạp hơn Modbus nhưng an toàn/giàu thông tin hơn |

**Điểm dễ nhầm**: CEMS tự nó đã hoàn chỉnh (đo → tính → lưu → xuất báo cáo), nhưng "hoàn chỉnh" không có nghĩa dữ liệu tự động chảy sang hệ thống tích hợp khác. Muốn lấy dữ liệu, lớp tích hợp (Adapter) phải kết nối và đọc đúng protocol mà thiết bị hỗ trợ — CEMS và hệ thống đích "nói 2 ngôn ngữ khác nhau", Adapter là lớp phiên dịch.

**Hệ quả thiết kế**: Adapter cần hỗ trợ tối thiểu Modbus RTU (phổ biến nhất), có khả năng mở rộng OPC-UA nếu tích hợp nhà máy lớn dùng DCS.

## 5. QA/QC & Calibration — kiểm soát độ tin cậy dữ liệu

**Vấn đề gốc**: Cảm biến không đo đúng mãi mãi — kết quả có thể "trôi" (drift) theo thời gian do hao mòn, bụi bám, nhiệt độ thay đổi.

### 3 lớp kiểm soát

| Cơ chế | Mô tả | Ngưỡng chấp nhận |
|---|---|---|
| **Daily Calibration** | Bơm zero gas (0 ppm) và span gas (nồng độ biết trước) hàng ngày, tự động tại chỗ | Drift < 2% của span |
| **Drift** | Độ lệch giữa giá trị đọc và giá trị chuẩn đã biết. Theo dõi riêng zero drift và span drift | 5 ngày liên tiếp vượt 2% → dữ liệu "out-of-control", không dùng cho báo cáo/tính toán |
| **RATA** | Đội kiểm định độc lập đo song song theo định kỳ (quý), so sánh độ chính xác | Accuracy ≥ 90% cho SO2, NOx (cần đối chiếu QCVN 19:2024) |

**Vì sao quan trọng với hệ thống tích hợp**: Nếu Direction 1 lấy dữ liệu CEMS làm input tính carbon credit, hệ thống không nên chỉ đọc con số ppm/mg mà cần đọc kèm trạng thái calibration — dữ liệu trong lúc drift vượt ngưỡng không đáng tin, không nên đưa vào emission reduction. Bỏ qua QA/QC có thể dẫn tới tính carbon credit dựa trên dữ liệu sai mà không biết ("garbage in, garbage out" nhưng bề ngoài vẫn hợp lệ).

## 6. Khung pháp lý Việt Nam

- **QCVN 19:2024/BTNMT** — quy chuẩn kỹ thuật quốc gia về khí thải công nghiệp, thay QCVN 19:2009, yêu cầu dữ liệu truyền tự động về cơ quan quản lý; chỉ tổ chức có giấy phép mới được đo/hiệu chuẩn CEMS.
- Có lộ trình áp dụng riêng cho cơ sở mới và cơ sở hiện hữu.

**Cần verify thêm**: mốc thời gian hiệu lực, lộ trình chi tiết, ngưỡng RATA/drift cụ thể theo QCVN 19:2024 (so với Thông tư 10/2021/TT-BTNMT) — chưa đối chiếu đầy đủ.

## 7. Hai hướng ứng dụng có thể có

**Hướng A — Compliance Reporting**: Hỗ trợ nhà máy tuân thủ QCVN 19:2024, dữ liệu phục vụ báo cáo định kỳ cho cơ quan quản lý. Cần hiểu sâu định dạng báo cáo bắt buộc, tần suất, yêu cầu pháp lý về độ chính xác.

**Hướng B — Data Integration cho Carbon Credit**: Hệ thống là tầng trung gian (middleware/oracle), nhận dữ liệu từ CEMS (hoặc cảm biến IoT tự lắp nếu nhà máy chưa có CEMS), xác thực tính toàn vẹn, đẩy vào nền tảng carbon credit (liên kết Direction 2). Cần nghiên cứu sâu data integrity/anchoring, schema normalization đa nguồn, cơ chế liên kết với emission reduction quantification.

**Với nhà máy chưa có CEMS**: IoT engineer đảm nhiệm thêm toàn bộ khâu thu thập — thiết kế firmware, kết nối cảm biến, xử lý calibration tại edge. **Đây là tình huống thực tế hiện tại** (đã chọn hướng CEMS in-situ tự lắp).

## 8. Rủi ro kỹ thuật cần lưu ý

- **Trust boundary (Oracle problem)**: Middleware chỉ xác thực tính toàn vẹn đường truyền, không xác thực độ chính xác cảm biến gốc — không mặc nhận dữ liệu qua middleware là "đúng tuyệt đối".
- **Sai đơn vị khi mapping schema** (concentration vs mass flow) — khó phát hiện bằng validation thông thường vì số liệu vẫn "hợp lý" trên bề mặt.
- **DAHS khóa API**: một số vendor không mở cho bên thứ ba — cần xác nhận với từng vendor cụ thể.
- **Adapter không đủ generic**: nếu viết cứng cho 1 vendor, phải viết lại khi mở rộng sang vendor khác — cần thiết kế theo adapter pattern ngay từ đầu.

## Vai trò IoT/data engineer (cả 2 hướng)

Không dừng ở "lắp đặt thiết bị + viết firmware". CEMS thương mại giải quyết bước thu thập (measurement), nhưng không tự động giải quyết:
1. Đọc protocol công nghiệp và convert sang hệ thống tích hợp
2. Gộp dữ liệu từ nhiều CEMS/vendor khác nhau về 1 schema thống nhất
3. Domain validation dựa trên đặc tính vật lý cảm biến
4. Đảm bảo khả năng mở rộng khi có nhiều nhà máy khách hàng dùng CEMS khác nhau

---

*Tài liệu này thay thế `tong_quan_huong1.md` (bản v1) — nội dung đầy đủ và chính xác hơn về kiến trúc CEMS thật, Protocol, QA/QC, khung pháp lý.*
