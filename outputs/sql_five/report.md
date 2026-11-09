# Kết quả chạy 5 câu hỏi bằng Gemini → SQL → DuckDB

Chọn đúng 5 dòng đầu tiên của `dataset/train/questions.jsonl`, không chọn theo đáp án. 
Model: `gemini-3.5-flash`; tối đa 3 mẫu, yêu cầu 2 mẫu đồng thuận. 
DuckDB được dựng lại bằng các adapter hiện có cho từng business. Đáp án chuẩn chỉ dùng sau suy luận.

**Kết quả: 4/5 đúng (80%), 1 sai, 0 abstain; PA = 0.60.**

| ID | Business | Dự đoán | Đáp án chuẩn | Kết quả |
|---|---|---:|---:|---|
| q_001883280e83 | biz_128 | 89526400 | 89526400 | correct |
| q_00195e1e4049 | biz_124 | 3039800 | 2954200 | wrong |
| q_002d03ebbded | biz_120 | 10309400 | 10309400 | correct |
| q_00558c1410ef | biz_105 | 2.08 | 2.08 | correct |
| q_006c26c65d50 | biz_112 | 2345 | 2345 | correct |

## Kiểm tra thực thi

- Cả 5 câu đều có 2 kết quả đồng thuận; không có lỗi sinh/thực thi SQL được ghi nhận.
- Đã chạy lại cả 5 SQL đã lưu ở chế độ chỉ đọc: kết quả trùng log, hash database trùng lần suy luận.
- File `replay_checks.json` ghi kết quả kiểm tra; `run_metadata.json` ghi lệnh chạy và phiên bản thư viện.
- Đây là 5 câu kiểm tra luồng, không đại diện cho độ chính xác trên toàn bộ 3.000 câu.

## Phân tích câu sai

`q_00195e1e4049` — doanh thu thuốc hoạt chất Natri clorid trong tháng 5/2026 đến ngày 25.

SQL do model sinh bỏ sót hai quy tắc nghiệp vụ:
- Chưa loại dòng chi tiết giống hệt nhau của cùng đơn theo rulebook §8: cộng dư 80.000 đồng.
- Chưa nhận diện trạng thái `Không giao được` là đơn hủy: cộng dư 5.600 đồng.

Truy vấn chẩn đoán bổ sung hai điều kiện trên cho ra 2.954.200 đồng, trùng đáp án chuẩn. 
**Đây là kiểm tra sau khi chấm, không phải kết quả model của lần chạy. Không sửa dự đoán hoặc điểm số gốc.**
Xem [SQL chẩn đoán](diagnostics/q_00195e1e4049.sql).

## Chi tiết từng câu

### q_001883280e83 — biz_128

- Câu gốc: tháng này tổng giá trị các phiếu nhập hàng là bao nhiu
- Rewrite: Tháng này tổng giá trị các phiếu nhập hàng là bao nhiêu
- As-of: 2026-09-07
- Type: `number`; dự đoán: **89526400**; chuẩn: **89526400**; **correct**.
- Đồng thuận: 2; thời gian (gồm ingestion): 53.892 giây.
- Giải thích của model: Tính tổng giá trị phiếu nhập (cột can_tra_ncc) từ danh_sach_nhap_hang_sheet1 trong tháng này (từ 2026-09-01 đến 2026-09-07 theo as_of_date 2026-09-07), loại bỏ phiếu hủy.
- [SQL đã chạy](sql/q_001883280e83.sql)

```sql
SELECT COALESCE(SUM(can_tra_ncc), 0) FROM danh_sach_nhap_hang_sheet1 WHERE vn_date(CAST(thoi_gian AS VARCHAR)) >= DATE '2026-09-01' AND vn_date(CAST(thoi_gian AS VARCHAR)) <= DATE '2026-09-07' AND vn_norm(CAST(trang_thai AS VARCHAR)) NOT IN (vn_norm('hủy'), vn_norm('đã hủy'))
```

### q_00195e1e4049 — biz_124

- Câu gốc: Thuốc có hoạt chất Natri clorid tháng này bán được tổng cộng bao nhiêu tiền?
- Rewrite: Thuốc có hoạt chất Natri clorid tháng này bán được tổng cộng bao nhiêu tiền?
- As-of: 2026-05-25
- Type: `number`; dự đoán: **3039800**; chuẩn: **2954200**; **wrong**.
- Đồng thuận: 2; thời gian (gồm ingestion): 101.038 giây.
- Giải thích của model: Tính tổng doanh thu (thành tiền các dòng hàng) từ các đơn hàng không bị hủy/trả trong tháng này (từ 2026-05-01 đến 2026-05-25) đối với các mặt hàng thuộc nhóm thuốc có hoạt chất Natri clorid.
- [SQL đã chạy](sql/q_00195e1e4049.sql)

```sql
SELECT COALESCE(SUM(c.thanh_tien), 0) AS doanh_thu FROM ban_hang_chi_tiet_hoa_don_sheet1 c JOIN ban_hang_hoa_don_sheet1 h ON c.ma_giao_dich = h.order_id JOIN ban_hang_thuoc_sheet1 t ON c.ma_hang = t.ma_hh WHERE vn_norm(t.hoat_chat) = vn_norm('Natri clorid') AND vn_norm(h.trang_thai) NOT IN (vn_norm('Đã hủy'), vn_norm('Hủy'), vn_norm('Đổi trả'), vn_norm('Trả hàng')) AND vn_date(CAST(h.ngay_ban AS VARCHAR)) BETWEEN DATE '2026-05-01' AND DATE '2026-05-25'
```

### q_002d03ebbded — biz_120

- Câu gốc: Tổng doanh thu trong ngày hôm nay là bao nhiêu?
- Rewrite: Tổng doanh thu trong ngày hôm nay là bao nhiêu?
- As-of: 2025-04-30
- Type: `number`; dự đoán: **10309400**; chuẩn: **10309400**; **correct**.
- Đồng thuận: 2; thời gian (gồm ingestion): 90.238 giây.
- Giải thích của model: Tính tổng doanh thu (tổng thành tiền các dòng sản phẩm chi tiết) của các đơn hàng không bị hủy hoặc trả hàng trong ngày 2025-04-30.
- [SQL đã chạy](sql/q_002d03ebbded.sql)

```sql
WITH all_orders AS (SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_01_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_02_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_03_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_04_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_05_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_06_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_07_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_08_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_09_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_10_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_11_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_12_2025 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_01_2026 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_02_2026 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_03_2026 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_04_2026 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_05_2026 UNION ALL SELECT ma_hoa_don, thoi_gian, tong_tien_hang, giam_gia, trang_thai FROM danh_sach_hoa_don_06_2026), valid_orders AS (SELECT ma_hoa_don FROM all_orders WHERE vn_date(thoi_gian) = CAST('2025-04-30' AS DATE) AND vn_norm(trang_thai) NOT IN ('huy don', 'tra hang', 'da tra hang', 'huy')) SELECT COALESCE(SUM(dt.thanh_tien), 0) FROM danh_sach_hoa_don_chi_tiet_sheet1 dt JOIN valid_orders vo ON dt.ma_hoa_don = vo.ma_hoa_don
```

### q_00558c1410ef — biz_105

- Câu gốc: tỉ lệ huỷ đơn tháng 6/2025 là nhiêu %
- Rewrite: Tỷ lệ hủy đơn tháng 6/2025 là bao nhiêu %
- As-of: 2026-06-24
- Type: `percent`; dự đoán: **2.08**; chuẩn: **2.08**; **correct**.
- Đồng thuận: 2; thời gian (gồm ingestion): 57.267 giây.
- Giải thích của model: Tính tỷ lệ đơn hủy trong tháng 6/2025 bằng tổng số đơn bị hủy (trạng thái hủy/đã hủy đơn) chia cho tổng số đơn hàng trong tháng 6/2025.
- [SQL đã chạy](sql/q_00558c1410ef.sql)

```sql
WITH all_sales AS (SELECT ma_hoa_don, status, ngay_giao_dich FROM so_ban_hang_1_thang_06_2025), orders AS (SELECT ma_hoa_don, MAX(CASE WHEN LOWER(COALESCE(status, '')) IN ('hủy', 'đã hủy đơn') THEN 1 ELSE 0 END) AS is_cancelled FROM all_sales WHERE ma_hoa_don IS NOT NULL AND ngay_giao_dich IS NOT NULL AND vn_date(CAST(ngay_giao_dich AS VARCHAR)) BETWEEN DATE '2025-06-01' AND DATE '2025-06-30' GROUP BY ma_hoa_don) SELECT ROUND(100.0 * SUM(is_cancelled) / COUNT(*), 2) AS cancellation_rate FROM orders
```

### q_006c26c65d50 — biz_112

- Câu gốc: thang 7/2026 co bao nhiu don co dthu tu 55.000 dong tro len
- Rewrite: Tháng 7/2026 có bao nhiêu đơn có doanh thu từ 55.000 đồng trở lên
- As-of: 2026-07-31
- Type: `number`; dự đoán: **2345**; chuẩn: **2345**; **correct**.
- Đồng thuận: 2; thời gian (gồm ingestion): 49.126 giây.
- Giải thích của model: Tính số đơn hàng không bị hủy hoặc trả hàng trong tháng 7/2026 có tổng thành tiền từ 55.000 đồng trở lên.
- [SQL đã chạy](sql/q_006c26c65d50.sql)

```sql
WITH excluded_orders AS (SELECT DISTINCT ma_don_hang FROM so_chi_tiet_ban_hang_2026_t07_2026 WHERE vn_norm(CAST(trang_thai AS VARCHAR)) IN ('cancel', 'da huy don', 'tra hang', 'hoan tra')), order_revenue AS (SELECT ma_don_hang, SUM(thanh_tien) AS doanh_thu FROM so_chi_tiet_ban_hang_2026_t07_2026 WHERE vn_date(CAST(ngay AS VARCHAR)) BETWEEN '2026-07-01' AND '2026-07-31' AND ma_don_hang NOT IN (SELECT ma_don_hang FROM excluded_orders) GROUP BY ma_don_hang) SELECT COUNT(*) FROM order_revenue WHERE doanh_thu >= 55000
```

