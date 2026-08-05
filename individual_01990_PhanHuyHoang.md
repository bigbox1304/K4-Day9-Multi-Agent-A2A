# Báo cáo cá nhân — Phan Huy Hoàng

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
|---|---|
| Họ và tên | Phan Huy Hoàng |
| MSSV | 2A202601990 |
| Khóa/Lớp | K4 |
| Vai trò chính | QA, Trace & Submission Engineer |
| Ngày hoàn thành | 2026-08-05 |

## 2. Phạm vi công việc

| Deliverable | File/hàm | Mục tiêu | Trạng thái |
|---|---|---|---|
| Output validation | `src/agents/verifier.py` | Schema, enum, ID, limits | Hoàn thành |
| Trace | `logging/trace.jsonl` | Trace 50 case | Hoàn thành |
| Metadata | `logging/metadata.json` | Model, parameter size, runtime | Cần cập nhật model cuối |
| Submission zip | `output/` | Đúng 50 JSON, không file lạ | Hoàn thành |

## 3. Checklist kiểm tra

- Đủ 50 file từ `EC_001.json` đến `EC_050.json`.
- `case_id` khớp tên file.
- `primary_issue` thuộc taxonomy README.
- `case_status` chỉ là `action_required` hoặc `no_action`.
- Evidence tồn tại trong CSV và đúng format.
- Responsible parties là object hợp lệ.
- Không vượt giới hạn array.
- Confidence nằm trong `[0, 1]`.
- Zip chỉ chứa 50 JSON output.
- `.env` không được commit.

## 4. Luồng kiểm thử

```text
Run pipeline → Validate JSON/schema → Validate IDs/evidence
             → Validate money/timestamps/null → Count files → Zip output
```

Một output có thể parse được nhưng vẫn bị 0 điểm nếu dùng text tự do như `under_investigation`, `Delivery variance` hoặc action dạng câu văn. Vì vậy cần validate cả enum nghiệp vụ.

## 5. Cách xác minh

```powershell
python main.py
(Get-ChildItem output -Filter "EC_*.json").Count
Compress-Archive -Path output\EC_*.json -DestinationPath output_final.zip
```

Zip cuối phải có đúng 50 entry và không có `.env`, source code, trace hoặc file audit.

## 6. Quyết định kỹ thuật

Tách `logging/` khỏi zip nộp bài để trace phục vụ audit nhưng không làm zip chứa file lạ. Metadata phải ghi đúng model thực tế và parameter size; không để `Unknown` nếu bài yêu cầu model dưới 10B.

## 7. Cam kết

- [x] Đã điền đúng họ tên và MSSV.
- [x] Không đưa secret vào báo cáo.
- [x] Có checklist kiểm tra trước khi nộp.
- [ ] Xác nhận lại output zip cuối cùng trước khi gửi.
