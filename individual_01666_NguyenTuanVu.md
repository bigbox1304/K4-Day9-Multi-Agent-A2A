# Báo cáo cá nhân — Nguyễn Tuấn Vũ

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
|---|---|
| Họ và tên | Nguyễn Tuấn Vũ |
| MSSV | 2A202601666 |
| Khóa/Lớp | K4 |
| Vai trò chính | AI Engineer — Coordinator & Agent Orchestration |
| Ngày hoàn thành | 2026-08-05 |

## 2. Phạm vi công việc

| Module | File/hàm phụ trách | Input | Output | Trạng thái |
|---|---|---|---|---|
| Coordinator | `main.py`, `src/agents/coordinator.py` | 50 input cases | Case workflow và output JSON | Hoàn thành |
| LLM integration | `src/llm_client.py` | Prompt và case context | Agent response | Hoàn thành |
| Agent handoff | `src/agents/` | Domain results | Customer/order/payment/delivery handoffs | Hoàn thành |
| Architecture | `architecture.md` | System design | Role và handoff diagram | Hoàn thành |

## 3. Thiết kế kỹ thuật

Coordinator nhận từng input case, lấy `claimed_order_id`, tạo context và phân công cho các agent theo domain. Các agent bàn giao kết quả có cấu trúc cho agent tổng hợp; kết quả được ghi vào `output/EC_xxx.json` và trace vào `logging/trace.jsonl`.

```text
Input case → Coordinator → Customer Agent
                         → Order/Product Agent
                         → Payment Agent
                         → Delivery Agent
                         → Policy/Resolution Agent
                         → Verifier → Output JSON
```

Mỗi agent có prompt và phạm vi dữ liệu riêng. Agent không được tự tạo ID, timestamp, evidence hoặc sự kiện không tồn tại trong CSV.

## 4. Contract

| Thành phần | Mô tả |
|---|---|
| Input | `input/EC_001.json` đến `input/EC_050.json` |
| Dữ liệu | 9 CSV Olist trong `data/` |
| Output | Một JSON tương ứng trong `output/` |
| Trace | `logging/trace.jsonl` |
| Metadata | `logging/metadata.json` |

## 5. Quyết định kỹ thuật

Chọn kiến trúc nhiều agent chuyên môn thay vì một prompt duy nhất để có handoff, trace và khả năng kiểm tra từng domain. Các phép tính tiền/thời gian phải lấy từ dữ liệu có thể kiểm chứng; LLM chỉ phân tích và trao đổi kết quả theo contract.

## 6. Lỗi đã xử lý

Các lỗi xử lý gồm malformed JSON, thiếu field, API timeout, missing order và null timestamp. Structured output, validation và trace per-agent được dùng để theo dõi và sửa lỗi.

## 7. Cách xác minh

```powershell
python main.py
```

Trước khi nộp cần kiểm tra model trong `logging/metadata.json` có không quá 10B parameters.

## 8. Cam kết

- [x] Báo cáo không chứa API key hoặc secret.
- [x] Vai trò phản ánh phần orchestration và agent handoff.
- [ ] Đã xác nhận tên model và kết quả chạy cuối cùng trước khi nộp.
