-- Diagnostic after scoring; NOT the SQL produced by the model in this run.
-- Rulebook section 8: identical rows of the same order count once.
-- Map the observed cancellation label and returned status from this business.
WITH details AS (SELECT DISTINCT * FROM ban_hang_chi_tiet_hoa_don_sheet1),
headers AS (SELECT DISTINCT * FROM ban_hang_hoa_don_sheet1)
SELECT SUM(c.thanh_tien) AS revenue
FROM details c
JOIN headers h ON c.ma_giao_dich = h.order_id
JOIN ban_hang_thuoc_sheet1 t ON c.ma_hang = t.ma_hh
WHERE vn_norm(t.hoat_chat) = vn_norm('Natri clorid')
  AND vn_date(h.ngay_ban) BETWEEN DATE '2026-05-01' AND DATE '2026-05-25'
  AND vn_norm(h.trang_thai) NOT IN ('khong giao duoc', 'doi tra');
