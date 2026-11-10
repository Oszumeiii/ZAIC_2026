# Ingestion — tài liệu bàn giao cho team

Tài liệu này giải thích ingestion đang làm gì, dữ liệu đi qua những bước nào và các phần khác cần nhận đầu ra nào. Dành cho thành viên mới đọc trước khi tìm hiểu code. Các đường dẫn và lệnh bên dưới tính từ thư mục gốc repository.

## 1. Ingestion giải quyết việc gì?

Đầu vào là folder dữ liệu của một doanh nghiệp: có thể gồm CSV, Excel nhiều sheet, SQLite và README mô tả nghiệp vụ. Các doanh nghiệp có thể dùng tên cột, định dạng ngày, dấu thập phân và cấu trúc bảng khác nhau.

Ingestion đưa những dữ liệu đó vào **một DuckDB riêng cho mỗi doanh nghiệp**, giữ khả năng đối chiếu về nguồn và tạo thêm những view thuận tiện cho truy vấn khi đủ bằng chứng.

Hai đầu ra chính để bàn giao cho phần truy vấn:

- **`data.duckdb`**: dữ liệu, view và metadata thực tế.
- **`DATABASE_GUIDE.md`**: hướng dẫn đọc database của đúng doanh nghiệp đó, để người phát triển hoặc model biết nên dùng bảng/cột nào và cần chú ý điều gì.

Phần ingestion dùng Python và SQL, không gọi LLM, không cần API key. Nó chuẩn bị dữ liệu; việc hiểu câu hỏi, tạo truy vấn và trả lời nằm ở các phần phía sau.

## 2. Những khái niệm cần hiểu trước

| Khái niệm | Ý nghĩa trong hệ thống | Ví dụ |
|---|---|---|
| Bảng nguồn logic | Một tập bản ghi đọc được từ nguồn | Một CSV, một sheet Excel hoặc một bảng SQLite |
| Schema | Nhóm các bảng/view bên trong cùng database | `raw`, `typed`, `semantic`, `meta` |
| View | Câu SQL được lưu thành một đối tượng có thể SELECT | View gộp các sheet theo tháng |
| Mức chi tiết, hay grain | Một dòng đang đại diện cho cái gì | Một đơn hàng, một dòng hàng hoặc một lần thanh toán |
| Khóa đơn / khóa ghép | Một cột / tổ hợp cột dùng nhận diện bản ghi | Mã đơn; hoặc mã đơn + thứ tự dòng |
| Quan hệ nhiều–một | Nhiều dòng bên nguồn tham chiếu một dòng bên đích | Nhiều dòng hàng thuộc một đơn hàng |
| Truy vết, hay lineage | Cách đi từ kết quả đã xử lý về bản ghi nguồn | Typed → Raw → file, sheet, dòng |
| Metadata | Thông tin mô tả dữ liệu và quá trình xử lý | Kiểu cột, hash nguồn, cảnh báo, bằng chứng quan hệ |

**Mức chi tiết quyết định cách tính.** Một đơn có ba dòng hàng thì bảng chi tiết có thể có ba dòng. `COUNT(*)` trên bảng đó đếm dòng hàng, không tự động đếm số đơn.

## 3. Toàn bộ luồng từ input đến output

```text
train/ hoặc public_test/
├── rulebook.md             Quy tắc chung của split, nếu có
├── calendar.csv            Lịch dùng chung, nếu có
└── data/
    └── <business_id>/
        ├── README.md       Mô tả doanh nghiệp và cột
        └── Các file nguồn
                  │
                  ▼
  Bước 1: Đọc bối cảnh, cấu hình và các file dữ liệu
                  │
                  ▼
  Bước 2: Giữ giá trị nguồn và vị trí bản ghi — Raw
                  │
                  ▼
  Bước 3: Xác định / chuyển kiểu cột — Typed
                  │
                  ▼
  Bước 4: Phân tích cột, khóa và quan hệ — đợt 1
                  │
                  ▼
  Bước 5: Gộp theo kỳ và JOIN đủ điều kiện — đợt 2
                  │
                  ▼
  Bước 6: Tách đơn–chi tiết, tổng hợp nguồn — đợt 3
                  │
                  ▼
  Kiểm tra và lưu database → sinh DATABASE_GUIDE.md
                  │
                  ├── Dev: xuất thêm các báo cáo
                  └── Production: database + hướng dẫn
```

Lịch được xuất riêng trong `_shared/data.duckdb`. Không sao chép lịch vào từng database doanh nghiệp. Các kiểm tra được thực hiện trong quá trình phân tích/tạo view, không chỉ ở cuối luồng.

### Bước 1 — Đọc nguồn và bối cảnh

**Input:** folder của một doanh nghiệp, README, rulebook và cấu hình override nếu có.

**Xử lý:**

- CSV: xác định encoding, dấu phân cách và dòng tiêu đề; cho phép khai báo tường minh khi tự nhận diện chưa đúng.
- Excel `.xlsx` / `.xlsm`: đọc các sheet, giá trị cache, thông tin công thức và định dạng ô. Không tự chạy Excel để tính công thức; `.xls` cũ chưa được hỗ trợ.
- SQLite: mở chỉ đọc và đọc các bảng cùng thông tin định vị bản ghi.
- Đọc README để bổ sung mô tả cột; không dùng đáp án train để quyết định biến đổi dữ liệu khi xuất database.

**Output:** các bảng nguồn logic cùng thông tin file, sheet/bảng, header và vị trí dòng.

Một file Excel nhiều sheet có thể tạo nhiều bảng logic. Sau khi tổ chức, số view nghiệp vụ có thể nhiều hoặc ít hơn số file nguồn.

### Bước 2 — Lưu lớp Raw

**Input:** các bản ghi vừa đọc.

**Xử lý:** giữ giá trị đã đọc, vị trí nguồn, dấu vết file và các cờ nghi vấn. Phần trước header được lưu trong metadata thay vì tính là bản ghi dữ liệu.

**Output:** các bảng `raw.t_<hash>` và metadata nguồn.

Raw giúp kiểm chứng và chạy lại chuyển kiểu. Raw **không phải bản sao nhị phân của Excel/CSV**; vẫn cần giữ file nguồn để xem định dạng gốc hoặc đọc lại dữ liệu.

### Bước 3 — Tạo lớp Typed

**Input:** Raw, tên/mô tả cột và cấu hình chuyển kiểu.

| Loại dữ liệu | Cách xử lý | Lý do |
|---|---|---|
| Mã hàng, mã khách, điện thoại… | Giữ chuỗi; bảo toàn số 0 đầu khi còn căn cứ | Mã không phải số để cộng/trừ |
| Số | Parse theo bằng chứng định dạng, dùng Decimal / SQL DECIMAL | Hạn chế sai số và nhầm dấu thập phân |
| Ngày và giờ | Chuyển về timestamp khi nhận diện được; giữ thành phần giờ | Tránh làm mất thời điểm giao dịch |
| Số serial ngày Excel | Cần xác định epoch phù hợp | Không tự đoán mốc ngày |
| Số lượng kèm đơn vị | Giữ cột gốc, tách thêm `__quantity` và `__unit` khi nhận diện được | Chưa đủ căn cứ để tự quy đổi đơn vị |
| Giá trị chuyển kiểu thất bại | Typed nhận NULL và ghi cảnh báo; Raw giữ giá trị | Tránh biến dữ liệu chưa hiểu thành một con số sai |
| Dòng tổng, tiêu đề lặp, nghi trùng | Gắn cờ, giữ bản ghi | Tránh xóa dữ liệu hợp lệ do nhận diện nhầm |

**Output:** bảng `typed.t_<hash>`, metadata từng cột, cảnh báo và ID truy vết.

Ví dụ minh họa, không phải cam kết tự nhận diện đúng mọi nguồn:

- Mã `"00123"` vẫn là chuỗi `"00123"`.
- `"3 chai"` có thể được giữ nguyên và tách thêm số lượng `3`, đơn vị `"chai"`; không tự đổi thành lít.
- Chuỗi `"1.234"` chỉ chuyển theo chính sách phân cách đã có căn cứ; trường hợp mơ hồ phải giữ cảnh báo/NULL hoặc cấu hình rõ.
- Thời điểm `2025-01-01 06:03:59` giữ cả giờ khi parse thành công.

Cột kỹ thuật `__ingest_row_id` nối Typed về `raw.row_id`. Đây là ID của bản ghi trong lần đọc nguồn, **không phải mã đơn hoặc khóa nghiệp vụ**. Nếu trùng tên cột nguồn, hệ thống thêm dấu `_` để tránh va chạm.

### Bước 4 — Phân tích cột, khóa và quan hệ

**Input:** Typed, metadata và các cảnh báo đã có.

**Xử lý:** thống kê số dòng, NULL, chuỗi trắng, giá trị phân biệt, trùng và lỗi parse; kiểm tra khóa đơn/ghép trong phạm vi tìm kiếm được cấu hình.

Quan hệ chỉ được chấp nhận khi có đủ bằng chứng, bao gồm ý nghĩa cột tương thích, kiểu phù hợp, tính duy nhất bên đích, độ bao phủ mã tham chiếu và không có đích cạnh tranh chưa giải quyết. Việc so mã dùng giá trị chính xác; không tự sửa mã để tạo một liên kết có vẻ khớp.

**Output:** metadata profile, ứng viên khóa, quan hệ và kết quả kiểm tra. Khóa `accepted` là kết luận theo snapshot và policy hiện tại, chưa phải PRIMARY KEY vật lý hoặc bảo đảm cho dữ liệu tương lai.

### Bước 5 — Gộp bảng theo kỳ và tạo view JOIN

**Input:** các bảng Typed và bằng chứng từ bước 4.

- **Gộp theo kỳ:** chỉ gộp khi cùng nhóm nguồn, cấu trúc/kiểu/ý nghĩa/đơn vị tương thích, có bằng chứng kỳ dữ liệu và không vi phạm điều kiện khóa. Dùng `UNION ALL`, giữ truy vết từng bản ghi; không tự xóa trùng.
- **JOIN:** tạo view `LEFT JOIN` theo quan hệ đủ điều kiện. Kiểm tra lại tính duy nhất, mã không khớp và khả năng giữ nguyên dữ liệu bên nguồn.

**Output:** view `merged_*`, `joined_*` khi đủ điều kiện và metadata kiểm chứng. Bảng Raw/Typed vẫn giữ để đối chiếu.

Một view JOIN giữ được số dòng bên nguồn vẫn có thể lặp số tiền bên cha. Không được cộng tiền cấp đơn trên view chi tiết chỉ vì JOIN đã vượt qua kiểm tra kỹ thuật.

### Bước 6 — Tách đơn–chi tiết và tổng hợp nguồn

**Input:** bảng/view nguồn đã đủ bằng chứng; ưu tiên nguồn gộp đã kiểm chứng để tránh xử lý trùng các kỳ thành viên.

- **Tách đơn–chi tiết:** xác định khóa đơn và các thuộc tính cấp đơn nhất quán. Tạo `orders_*`, `lines_*` rồi kiểm tra JOIN lại khôi phục được dữ liệu tương ứng, kể cả số lần xuất hiện của dòng trùng.
- **Tổng hợp:** tạo `aggregate_*` theo các khóa nhóm rõ ràng; giữ riêng trạng thái/đơn vị cần thiết. Có thể đếm bản ghi và lấy thời điểm đầu/cuối. SUM chỉ được thêm cho các số đo nguồn đủ điều kiện về kiểu, tính đầy đủ và đơn vị.

**Output:** view được áp dụng, quan hệ logic lines → orders, quyết định số đo và các lý do chưa áp dụng.

Aggregate nguồn chưa đồng nghĩa với doanh thu, công nợ hay số đơn hợp lệ. Các quy tắc hủy/trả hàng, ngày chốt `as_of_date`, làm tròn và định nghĩa chỉ tiêu vẫn cần được áp dụng đúng ở phần truy vấn nghiệp vụ.

### Bước 7 — Sinh hướng dẫn database

Sau khi database được lưu thành công, hệ thống đọc catalog và metadata để sinh `DATABASE_GUIDE.md`:

- Bối cảnh README/rulebook, bản đồ nguồn → tên SQL.
- Bảng/view, mức chi tiết và từ điển cột.
- Khóa/quan hệ đã chấp nhận, giới hạn sử dụng và cảnh báo.
- Phép biến đổi, group-by và quyết định số đo.
- SQL mẫu được kiểm tra tham chiếu bằng `EXPLAIN`, cùng hướng dẫn truy vết.
- Policy và fingerprint metadata/schema.

Hướng dẫn không phụ thuộc các báo cáo dev. Ý nghĩa chưa biết được ghi rõ; không dùng LLM để đoán. `EXPLAIN` kiểm tra truy vấn bind được, chưa xác nhận câu SQL giải đúng câu hỏi nghiệp vụ.

## 4. Trong một database có gì?

| Schema | Chứa gì? | Ai dùng? |
|---|---|---|
| `raw` | Giá trị đã đọc, ID/vị trí nguồn và cờ dòng | Debug, truy vết, retype |
| `typed` | Bản ghi đã chuyển kiểu, giữ tên cột nguồn | Bộ truy vấn dữ liệu nền |
| `semantic` | View đọc trực tiếp Typed và các view gộp/JOIN/tách/tổng hợp đủ điều kiện | Bộ truy vấn khi phù hợp mức chi tiết |
| `meta` | Bối cảnh, mapping, profile, cảnh báo, khóa/quan hệ, kiểm chứng | Bộ chọn dữ liệu, tài liệu và công cụ quan sát |

Bảng vật lý có tên `t_<hash>` để ổn định và tránh va chạm. Đọc `meta.tables` để biết tên đó tương ứng nguồn nào; không tự đoán hash từ tên file.

```sql
SELECT logical_name, db_name, lineage_column
FROM meta.tables
ORDER BY logical_name;
```

Truy vấn này chạy khi đã kết nối trực tiếp database của một doanh nghiệp. Khi dùng viewer có nhiều database được attach, thêm tên database ở trước `meta.tables` theo hướng dẫn viewer.

## 5. Ví dụ thực tế: biz_111

Nguồn nằm ở `train/data/biz_111/`, gồm README và năm CSV:

| Bảng logic | Nội dung để tham khảo theo nguồn | Số dòng ở snapshot đã xuất |
|---|---|---:|
| `don_hang` | Đơn hàng | 66.469 |
| `chi_tiet_don` | Dòng hàng của đơn | 106.585 |
| `khach_hang` | Khách hàng | 200 |
| `san_pham` | Sản phẩm | 50 |
| `thanh_toan` | Bản ghi thanh toán | 66.195 |

Các số trên là snapshot đã xử lý, không phải hằng số trong code. Hãy đọc lại metadata khi nguồn thay đổi.

Bốn quan hệ Typed được chấp nhận trên snapshot này:

```text
chi_tiet_don."Ma don" ── N:1 ──► don_hang."Mã chứng từ"
thanh_toan."Số HĐ"    ── N:1 ──► don_hang."Mã chứng từ"
chi_tiet_don."ID SP"  ── N:1 ──► san_pham."SKU"
don_hang."Số KH"      ── N:1 ──► khach_hang."ma_kh"
```

Tên trong sơ đồ là tên logic dễ đọc. Khi viết SQL, lấy tên bảng SQL thực tế từ `meta.tables` hoặc hướng dẫn của doanh nghiệp.

**Điểm quan trọng:** một đơn có thể có nhiều dòng hàng và nhiều lần thanh toán. Ví dụ giả định có 3 dòng hàng và 2 thanh toán, JOIN hai bảng theo mã đơn có thể tạo 6 dòng. Muốn kết hợp số đo hai bên, cần tổng hợp từng bên về cùng mức đơn trước khi JOIN và vẫn phải kiểm tra đơn vị/ý nghĩa số đo.

Snapshot này còn các ngày serial chưa xác định epoch và những cột tiền/số lượng chưa đủ điều kiện chuyển kiểu hoặc tổng hợp. Có quan hệ hợp lệ không có nghĩa toàn bộ dữ liệu đã sạch.

Mở [hướng dẫn biz_111](../outputs/ingestion/train/biz_111/DATABASE_GUIDE.md) hoặc [biz_102](../outputs/ingestion/train/biz_102/DATABASE_GUIDE.md) để xem đầy đủ. Các file trong `outputs/` được Git bỏ qua; đồng đội cần chạy ingestion hoặc nhận output được bàn giao để mở các liên kết này.

## 6. Dev và production khác nhau thế nào?

| Output | Dev | Production |
|---|---|---|
| `data.duckdb` | Có | Có |
| `DATABASE_GUIDE.md` | Có | Có |
| `REPORT.md`, `report.json` | Có | Không xuất |
| `ORGANIZATION*.md`, `organization*.json` | Có | Không xuất |
| `summary.json`, `REVIEW.md`, `review.json` | Có | Không xuất |
| Sơ đồ HTML | Tạo riêng khi cần | Không tự xuất |

Mặc định là dev. Production vẫn thực hiện phân tích và kiểm tra, giữ metadata trong `meta`, ghi lỗi ra terminal và trả mã lỗi khi thất bại.

Khi đổi một thư mục hiện có sang production, chương trình dọn đúng những tên báo cáo đã khai báo. Không tự xóa file người dùng, sơ đồ hay audit tùy chỉnh. Nên dùng một thư mục output production mới cho mỗi lần bàn giao.

## 7. Chạy thử trong vài phút

Dùng môi trường Python 3.10+; nếu chưa có môi trường ảo thì tạo trước:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-ingestion.txt
```

Chạy một doanh nghiệp ở chế độ dev:

```bash
.venv/bin/python -m app.ingestion.run \
  --split-dir train \
  --out-dir outputs/ingestion/train \
  --business biz_111 \
  --mode dev
```

Đọc `biz_111/DATABASE_GUIDE.md` để hiểu database; đọc `REPORT.md` và các báo cáo organization khi cần rà quá trình xử lý.

Chạy toàn bộ train ở production:

```bash
.venv/bin/python -m app.ingestion.run \
  --split-dir train \
  --out-dir outputs/production/train \
  --mode production
```

Với public test, đổi `--split-dir public_test` và chọn thư mục output tương ứng. Ingestion không sửa dataset nguồn. Chạy lại dựng database mới để thay database của doanh nghiệp được chọn; các view tự thêm trực tiếp vào database cũ không được bảo toàn qua full ingestion.

## 8. Khi sửa code hoặc dữ liệu, chạy lại lệnh nào?

| Thay đổi | Cách chạy lại |
|---|---|
| Nguồn, encoding, delimiter, header hoặc cách đọc file | Full ingestion |
| Chính sách chuyển kiểu, với nguồn còn khớp hash | Retype từ Raw |
| Chính sách phân tích/tổ chức quan hệ và view | Organization trên database có sẵn |
| Cách trình bày hướng dẫn database | Chỉ tạo lại guide |

```bash
# Dựng lại Typed và các bước phía sau; vẫn kiểm tra hash nguồn
.venv/bin/python -m app.ingestion.retype \
  --out-dir outputs/ingestion/train --mode dev

# Phân tích/tổ chức lại một database, không đọc lại CSV/Excel
.venv/bin/python -m app.ingestion.organization.run \
  --database outputs/ingestion/train/biz_111/data.duckdb --mode dev

# Chỉ tạo lại hướng dẫn, mở database read-only
.venv/bin/python -m app.ingestion.documentation.run \
  --database outputs/ingestion/train/biz_111/data.duckdb
```

Override là cấu hình dữ liệu mà bộ xử lý chung đọc vào, không phải chương trình riêng cho từng doanh nghiệp. Xem [overrides.example.json](../app/ingestion/overrides.example.json); chỉ khai báo định dạng/đơn vị khi có căn cứ từ nguồn. Toàn bộ các bước tự động có thể chạy mà không cần một người duyệt từng đề xuất.

## 9. Xem dữ liệu và quan hệ bằng cách nào?

```bash
# Xem dữ liệu các database ở outputs/ingestion qua DuckDB UI
.venv/bin/python scripts/view_ingestion.py
# Mở http://localhost:4213

# Xem sơ đồ các database đã xuất, chọn biz_111 ban đầu
.venv/bin/python scripts/view_relationships.py biz_111
# Mở http://localhost:4214
```

Giữ terminal chạy khi dùng giao diện. Đóng viewer trước khi cập nhật các database đang mở. Sơ đồ phân biệt quan hệ accepted, candidate và đường dẫn xuất; đường dẫn xuất không phải khóa ngoại.

Hai công cụ trên mặc định tìm database trong `outputs/ingestion`. Sơ đồ dùng màu xanh cho accepted, nét đứt cam cho candidate và đường xám cho nguồn dẫn xuất. Candidate chưa được xác nhận để sử dụng tự động.

## 10. Hợp đồng bàn giao cho phần truy vấn

```text
business_id
    ↓ chọn đúng thư mục
DATABASE_GUIDE.md + data.duckdb cùng lần xuất thành công
    ↓
Đọc bối cảnh → chọn bảng/view → kiểm tra mức chi tiết/đơn vị
    ↓
Tạo SQL → thực thi → kiểm tra kết quả → định dạng câu trả lời
```

Phần truy vấn cần:

1. Chọn đúng doanh nghiệp và đọc hướng dẫn đi kèm database của nó.
2. Dùng quan hệ đã đủ bằng chứng; không tự coi candidate là FK được xác nhận.
3. Xử lý NULL/cảnh báo, tránh cộng trùng giữa bảng nguồn và view dẫn xuất.
4. Áp dụng đúng quy tắc nghiệp vụ của câu hỏi: kỳ thời gian, trạng thái, ngày chốt, đơn vị, cách làm tròn.
5. Chỉ sử dụng bộ artifact sau khi lệnh xuất thành công. Database và Markdown không có transaction chung trên hai file.

File guide có thể lớn khi có nhiều sheet. Bộ tạo ngữ cảnh có thể chọn những mục liên quan thay vì gửi toàn bộ vào mỗi prompt. Fingerprint trong guide là dấu vết metadata/schema, không phải checksum toàn bộ byte database.

**Trạng thái tích hợp:** inference/evaluation mặc định dùng `SQLSolver`. SQL
context đọc catalog từ DuckDB và truyền schema vật lý, semantic mapping, facts,
relationships, unresolved/quality, view đã tạo, lineage và completeness theo cột
vào prompt. `DATABASE_GUIDE.md` không thay thế catalog. Chọn
`--query-engine pandas` để dùng Typed DataFrames từ cùng reader/type conversion
như baseline; hai chế độ không còn bộ đọc độc lập.

## 11. Code nằm ở đâu, sửa chỗ nào?

| File/thư mục | Trách nhiệm |
|---|---|
| `app/ingestion/sources/adapters/` | Đọc CSV/Excel/SQLite/Parquet và giữ provenance nguồn |
| `app/ingestion/sources/dispatch.py` — `SourceReader` | Điều phối adapter theo phần mở rộng file |
| `app/ingestion/raw.py` — `RawData` | Chuẩn hóa RawTable, vị trí, công thức và metadata nguồn |
| `app/ingestion/processing/normalization.py` — `IdentifierNormalizer`, `ValueNormalizer` | Nhận kiểu, parse, chính sách số/ngày/đơn vị, cờ dòng và chuẩn hóa định danh |
| `app/ingestion/layered.py` — `BusinessIngestion` | Ghép kết quả một doanh nghiệp, áp dụng cấu hình |
| `app/ingestion/store.py` — `IngestionStore` | Xuất database, chạy tổ chức và sinh hướng dẫn |
| `app/ingestion/organization/analyze.py` — `OrganizationAnalyzer` | Đợt 1: profile, khóa và quan hệ |
| `app/ingestion/organization/build.py` — `OrganizationBuilder` | Đợt 2: gộp kỳ và JOIN |
| `app/ingestion/organization/model.py` — `OrganizationModelBuilder` | Đợt 3: tách và tổng hợp nguồn |
| `app/ingestion/organization/workflow.py` — `OrganizationWorkflow` | Điều phối dựng lại các view và kiểm tra phụ thuộc |
| `app/ingestion/documentation/` — `DatabaseGuide` | Sinh và kiểm tra hướng dẫn database |
| `app/ingestion/artifacts.py` — `ArtifactPolicy` | Quy tắc output dev/production, danh sách báo cáo được dọn |
| `app/ingestion/run.py` — `IngestionCommand`; `app/ingestion/retype.py` — `RetypeService` | Các lệnh chạy ingestion và retype |
| `app/ingestion/review.py` | Tổng hợp cảnh báo cho dev |
| `tests/ingestion/` | Kiểm thử đọc/chuyển kiểu, tổ chức, hướng dẫn và công cụ xem |

## 12. Những giới hạn team cần nhớ

- `complete` nghĩa là đọc/xuất được, chưa xác nhận mọi ô dữ liệu đều đúng.
- `parse_failed` đếm theo ô; không cộng số cảnh báo để suy ra số dòng hỏng.
- Một FK accepted chỉ nói về bằng chứng trên snapshot theo policy, không bảo đảm mọi JOIN nghiệp vụ đều đúng.
- Khóa ghép chỉ được tìm trong phạm vi giới hạn; chưa phát hiện mọi loại mô hình và quan hệ.
- Thiếu epoch, format ngày hoặc đơn vị tiền thì hệ thống có thể chưa parse/tổng hợp được. Timestamp có timezone và một số định dạng ngày giờ chưa được hỗ trợ đầy đủ.
- Chưa tự quy đổi đơn vị, suy doanh thu/công nợ, hoặc chuẩn hóa mọi doanh nghiệp thành một schema cố định.
- SUM hợp lệ về kỹ thuật chưa đủ để gọi là một chỉ tiêu nghiệp vụ.
- Kiểm thử bảo toàn dữ liệu và SQL không thay thế việc đo chất lượng trả lời trên train. Chưa có kết luận ingestion làm tăng PA.

## Đọc tiếp

- Đọc `DATABASE_GUIDE.md` trong thư mục output của doanh nghiệp sau khi chạy ingestion.
- Mở `REPORT.md` và `ORGANIZATION*.md` trong output dev để xem quyết định cụ thể.
- Xem code và kiểm thử tại `app/ingestion/` và `tests/ingestion/`.
- Tài liệu này là tài liệu team được bàn giao cùng code; các slide và ghi chú phát triển bổ sung được quản lý riêng.
