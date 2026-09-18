# HƯỚNG DẪN & NGUYÊN TẮC LÀM VIỆC CỦA AGENT (AGENTS.MD)

> **Thư mục Skill bắt buộc:** `/Users/macbook/Documents/Pione/carbon-iot-huong1/.agents/skills`

---

## 1. MÔ HÌNH PHỐI HỢP & KỶ LUẬT SKILL

Mọi tác vụ được giao cho Agent trong repository này **BẮT BUỘC** phải tuân thủ nghiêm ngặt hệ thống skills đã được cài đặt trong `/Users/macbook/Documents/Pione/carbon-iot-huong1/.agents/skills`:

### A. Khởi tạo & Phản biện (Pre-Implementation)

1. **`using-superpowers`:** Kích hoạt skill phù hợp trước BẤT KỲ hành động hay câu hỏi nào. Tuyệt đối không suy diễn né tránh (no rationalization).
2. **`brainstorming` kết hợp `grilling` / `grill-me`:**
   - Phân loại rõ đường đi: **Spike** (thử nghiệm nhanh), **Bounded** (thay đổi nhỏ có sẵn luồng), hoặc **Architectural** (tính năng/hệ thống mới).
   - Chủ động phản biện, stress-test các giả định kỹ thuật, chất vấn các edge-cases trước khi chốt giải pháp.
3. **`using-git-worktrees`:** Luôn cô lập không gian làm việc trên nhánh/worktree độc lập, tránh sửa trực tiếp trên nhánh chính mà chưa được phép.

### B. Lập kế hoạch & Phê duyệt (Planning & Approval Gate)

1. **`writing-plans`:**
   - Lập tài liệu kế hoạch chi tiết tại `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`.
   - Chia nhỏ thành các bite-sized tasks (2–5 phút/bước) theo chuẩn TDD.
   - Định nghĩa rõ giao diện (Consumes / Produces), các file cần tạo/sửa.
   - **Tuyệt đối không dùng placeholder** (không `TODO`, `TBD`, "handle edge cases", hay mã giả).
2. **Hard Approval Gate:** Phải dừng lại và chờ đối tác (User) duyệt kế hoạch trước khi đụng vào code.

### C. Thực thi Chuyên Nghiệp (Execution Phase)

1. **`subagent-driven-development` (Khuyến nghị ưu tiên):**
   - Điều phối subagent chuyên biệt với context độc lập cho từng task.
   - Tự động review 2 tầng sau mỗi task: Tuân thủ spec (spec compliance) và chất lượng mã (code quality).
   - Thực thi liên tục theo nguyên tắc **"Rulings, not stalls"** (tự ra quyết định kỹ thuật dựa trên spec để tiếp tục, không hỏi dừng ngắt quãng vô ích; chỉ dừng khi gặp thao tác phá hủy hoặc rủi ro bảo mật).
2. **`dispatching-parallel-agents`:** Tận dụng chạy song song khi các task hoàn toàn độc lập, không dùng chung state.
3. **`executing-plans`:** Áp dụng khi thực thi tuần tự trong cùng một session.

### D. Tối giản Triệt để (Ponytail Ecosystem)

1. **`ponytail`:** Duy trì phản xạ senior developer tối giản trên mọi dòng code:
   - Thang tối giản 7 bậc: Cần thiết không? (YAGNI) $\rightarrow$ Đã có trong repo chưa? $\rightarrow$ Thư viện chuẩn (`stdlib`)? $\rightarrow$ Tính năng native của nền tảng? $\rightarrow$ Dependency đã cài? $\rightarrow$ 1 dòng code? $\rightarrow$ Code tối thiểu.
2. **`ponytail-review`:** Review diff chỉ tập trung diệt trừ over-engineering (`delete:`, `stdlib:`, `native:`, `yagni:`, `shrink:`), tính điểm theo `net: -N lines`.
3. **`ponytail-audit` & `ponytail-debt`:** Quét repo tìm code thừa và quản lý các nợ kỹ thuật có chủ đích (`ponytail:` comments).

### E. Kiểm thử & Bàn giao (Verification & Completion Phase)

1. **`test-driven-development` (TDD):**
   - **The Iron Law:** _NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST_.
   - Vòng lặp: RED (chạy thấy fail đúng lỗi) $\rightarrow$ GREEN (code tối thiểu để pass) $\rightarrow$ REFACTOR.
2. **`systematic-debugging`:** Sửa lỗi tận gốc rễ (root cause), không vá triệu chứng tại ngọn.
3. **`verification-before-completion` (LUẬT THÉP HOÀN THÀNH):**
   - **The Iron Law:** `NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE`.
   - Không tuyên bố "xong", "đã fix" hay tỏ vẻ hài lòng nếu chưa chạy lệnh kiểm thử ngay trong lượt phản hồi hiện tại và đọc output thực tế.
4. **`requesting-code-review` & `receiving-code-review`:** Đảm bảo code đạt tiêu chuẩn khắt khe trước khi tích hợp; tiếp nhận phản biện kỹ thuật có cơ sở.
5. **`finishing-a-development-branch`:** Chạy full test suite $\rightarrow$ Xác nhận trạng thái git worktree $\rightarrow$ Đưa menu 3 lựa chọn (Merge local, PR, Keep branch) $\rightarrow$ Dọn dẹp workspace sau khi user duyệt.

---
