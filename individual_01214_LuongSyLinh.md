# Báo cáo cá nhân — Lương Sỹ Linh

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
|---|---|
| Họ và tên | Lương Sỹ Linh |
| MSSV | 2A202601214 |
| Khóa/Lớp | K4 |
| Vai trò chính | Data & Policy Engineer |
| Ngày hoàn thành | 2026-08-05 |

## 2. Phạm vi công việc

| Module | File/hàm phụ trách | Input | Output | Trạng thái |
|---|---|---|---|---|
| Data loading | `src/agents/coordinator.py` | CSV Olist | Joined order context | Hoàn thành |
| Customer analysis | `src/agents/customer.py` | Customer/order rows | Customer history | Hoàn thành |
| Payment analysis | `src/agents/payment.py` | Items/payments | Reconciliation facts | Hoàn thành |
| Delivery analysis | `src/agents/delivery.py` | Order/item timestamps | Delivery facts | Hoàn thành |
| Policy mapping | `src/agents/policy.py` | Agent handoffs | Issue, refund, actions | Hoàn thành |

## 3. Cách triển khai

Order được truy xuất bằng `claimed_order_id`. Các bảng được join theo khóa trong README. `customer_unique_id` dùng để tìm order lịch sử; order lịch sử chỉ được đưa vào `customer_context.related_order_ids`.

Các phép tính chính:

```text
expected_total_brl = sum(price) + sum(freight_value)
difference_brl = sum(payment_value) - expected_total_brl
delivery_variance_hours = delivered_date - estimated_date
```

Primary issue được đối chiếu theo `EC_POLICY_V2`; không tạo refund ledger, tracking checkpoint hoặc evidence không tồn tại trong CSV.

## 4. Output contract

- `affected_entities` chỉ chứa order đang điều tra.
- Item ID có dạng `<order_id>:<order_item_id>`.
- Payment ID có dạng `<order_id>:<payment_sequential>`.
- Evidence dùng các prefix `order:`, `item:`, `payment:`, `seller:`, `policy:`.
- Giá tiền và số giờ làm tròn 2 chữ số.
- Order không có item dùng `null` cho reconciliation fields theo README.

## 5. Quyết định kỹ thuật

Tách data facts khỏi quyết định policy giúp agent không tự suy diễn tiền hoặc timestamp. Mọi kết luận phải truy ngược được về CSV và evidence ID.

## 6. Cách xác minh

```powershell
python main.py
```

Kiểm tra một case đại diện cho từng nhóm: canceled, unavailable, late seller, late logistics, split payment và unsupported claim.

## 7. Lỗi đã xử lý

Các lỗi cần kiểm tra gồm join sai nhiều dòng do order có nhiều item/payment, duplicate seller trong delivery analysis, null timestamp và payment không reconcile. Verifier phải bắt các lỗi này trước khi ghi file.

## 8. Cam kết

- [x] Đã điền đúng họ tên và MSSV.
- [x] Không sử dụng dữ liệu ngoài CSV để tạo evidence.
- [x] Không ghi API key hoặc secret.
