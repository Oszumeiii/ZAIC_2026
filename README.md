# ZAIC DataMate

Repo chuẩn bị dữ liệu doanh nghiệp cho bài toán hỏi đáp: khảo sát file nguồn, xây dựng **BusinessManifest**, kiểm tra mapping nghiệp vụ, nạp dữ liệu vào **DuckDB** và tạo **Business Catalog**.

Phần đã triển khai tập trung vào **ingestion, manifest và catalog**. Repo chưa có pipeline hoàn chỉnh nhận câu hỏi → sinh SQL → trả đáp án. Catalog hợp schema hoặc có nhiều cột được mapping chưa chứng minh đáp án nghiệp vụ sẽ đúng.

## Mục lục

1. [Kiến trúc và cấu trúc repo](#1-kiến-trúc-và-cấu-trúc-repo)
2. [Cài đặt môi trường](#2-cài-đặt-môi-trường)
3. [Chuẩn bị dữ liệu đầu vào](#3-chuẩn-bị-dữ-liệu-đầu-vào)
4. [Chạy nhanh với manifest có sẵn](#4-chạy-nhanh-với-manifest-có-sẵn)
5. [Luồng xử lý bên trong](#5-luồng-xử-lý-bên-trong)
6. [Tạo và cập nhật manifest](#6-tạo-và-cập-nhật-manifest)
7. [Audit trước ingestion](#7-audit-trước-ingestion)
8. [Nạp DuckDB và xây catalog](#8-nạp-duckdb-và-xây-catalog)
9. [Cấu trúc manifest, evidence và catalog](#9-cấu-trúc-manifest-evidence-và-catalog)
10. [Relationships và chất lượng nghiệp vụ](#10-relationships-và-chất-lượng-nghiệp-vụ)
11. [Sử dụng API class](#11-sử-dụng-api-class)
12. [Kiểm thử và kiểm chứng](#12-kiểm-thử-và-kiểm-chứng)
13. [Quy trình mở rộng repo](#13-quy-trình-mở-rộng-repo)
14. [Xử lý lỗi thường gặp](#14-xử-lý-lỗi-thường-gặp)
15. [Giới hạn hiện tại](#15-giới-hạn-hiện-tại)

## 1. Kiến trúc và cấu trúc repo

```text
README doanh nghiệp + file nguồn + Rulebook
                     │
              SourceInventory
                     │
          Inventory + evidence bundle
                     │
       ┌─────────────┴────────────────┐
       │                              │
BusinessManifestService       ManifestMigrator
    Sinh bằng Gemini          Nâng cấp offline
       │                              │
       └─────────────┬────────────────┘
                     │
              ManifestValidator
                     │
          BusinessManifest v2 + report
                     │
             IngestionPipeline
                     │
       DuckDB + CatalogBuilder + RelationshipService
                     │
       catalog.json + quality + unresolved
```

Cấu trúc mã nguồn:

```text
app/
├── ingestion/
│   ├── adapters/
│   │   ├── common.py          # SourceTable, TabularLayout
│   │   ├── csv_adapter.py     # CsvAdapter
│   │   ├── excel_adapter.py   # ExcelAdapter
│   │   ├── sqlite_adapter.py  # SqliteAdapter
│   │   └── parquet_adapter.py # ParquetAdapter
│   ├── source_inventory.py   # Khám phá file, đọc nguồn, khảo sát
│   ├── normalization.py      # Chuẩn hóa tên và giá trị
│   ├── pipeline.py           # IngestionPipeline, _TableWriter
│   ├── build_manifests.py    # CLI sinh manifest
│   ├── upgrade_manifests.py  # CLI migration
│   └── audit_manifests.py    # CLI audit
├── manifests/
│   ├── service.py            # BusinessManifestService
│   ├── migration.py          # ManifestMigrator
│   ├── validation.py         # ManifestValidator
│   └── commands.py           # BuildManifestsCommand, AuditManifestsCommand
├── catalog/
│   ├── builder.py            # CatalogBuilder
│   └── relationships.py      # RelationshipService
├── rules/
│   └── rulebook.py           # Rulebook
├── storage/
│   └── json_store.py         # JsonStore
└── llm/
    ├── gemini.py             # GeminiLLM
    └── prompts.py            # System prompt tạo manifest

schemas/
├── business_manifest.schema.json     # Chấp nhận v1 hoặc v2
├── business_manifest.v1.schema.json  # Hợp đồng v1
├── business_manifest.v2.schema.json  # Hợp đồng v2
└── catalog.schema.json              # Hợp đồng catalog

dataset/train/
├── rulebook.md
├── data/biz_*/                       # Dữ liệu gốc và README riêng
├── manifest_overrides/              # Các chỉnh sửa đã được rà soát
└── manifests/
    ├── biz_*.json
    ├── evidence/biz_*.json
    └── reports/                     # Báo cáo từng doanh nghiệp

tests/                               # Test class, adapter, manifest, CLI
docs/                               # Tài liệu kiến trúc và manifest v2
```

Mọi hàm Python trong mã ứng dụng và test đều thuộc class, kể cả helper. Các phép biến đổi không giữ trạng thái dùng `staticmethod`; `GeminiLLM.from_env()` dùng `classmethod`; `_TableWriter` giữ connection, cấu hình kiểu dữ liệu và bộ đếm cho một bảng.

| Nhóm | Class chính | Trách nhiệm |
| --- | --- | --- |
| Nguồn dữ liệu | `SourceInventory`, các adapter | Tìm file, đọc từng sheet/bảng, thu thập cấu trúc và mẫu |
| Chuẩn hóa | `IdentifierNormalizer`, `ValueNormalizer` | Tên vật lý, alias, suy luận kiểu lưu trữ và chuyển giá trị |
| Manifest | `BusinessManifestService`, `ManifestMigrator` | Tạo bằng LLM hoặc nâng cấp từ manifest hiện có |
| Validation | `ManifestValidator` | Đối chiếu nguồn, evidence, ràng buộc ngữ nghĩa, coverage |
| Catalog | `CatalogBuilder`, `RelationshipService` | Gắn semantics vào bảng vật lý và kiểm tra quan hệ |
| Luật | `Rulebook` | Tách luật và đánh giá các thành phần nghiệp vụ còn thiếu |
| Lưu trữ | `JsonStore` | Ghi JSON qua file tạm rồi thay thế file đích |

Ba file CLI manifest trong `app/ingestion` chỉ là entry point; implementation nằm trong `app/manifests`. Các import hàm độc lập của cấu trúc cũ đã được thay bằng API class.

## 2. Cài đặt môi trường

Thực hiện các lệnh bên dưới từ **thư mục gốc repo**, nơi chứa `README.md` và `requirements.txt`. Khuyến nghị dùng Python 3.11 trở lên; các kiểm tra trước đó được chạy bằng Python 3.13.

### Bước 1 — Tạo môi trường Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Trên Windows PowerShell, thay lệnh kích hoạt bằng:

```powershell
.venv\Scripts\Activate.ps1
```

| Thư viện | Vai trò trong repo |
| --- | --- |
| `google-genai`, `python-dotenv` | Gọi Gemini và đọc cấu hình `.env` |
| `duckdb` | Lưu bảng, đọc Parquet và kiểm tra dữ liệu quan hệ |
| `openpyxl` | Đọc `.xlsx`, `.xlsm` |
| `xlrd` | Đọc `.xls` |
| `jsonschema` | Kiểm tra manifest/catalog theo JSON Schema |

### Bước 2 — Cài công cụ test

`pytest` chưa nằm trong `requirements.txt`; cài riêng nếu cần chạy test:

```bash
python -m pip install pytest
```

### Bước 3 — Cấu hình Gemini khi cần sinh manifest mới

Migration, audit và ingestion chạy offline, không cần API key. Chỉ bước sinh mới bằng Gemini cần cấu hình này.

Tạo file `.env` tại thư mục gốc repo:

```dotenv
GEMINI_APIKEY=your_api_key_here
```

`GeminiLLM.from_env()` đọc đúng biến **`GEMINI_APIKEY`**. Model mặc định trong code là `gemini-3.5-flash-lite`; đây là cấu hình của repo, không phải cam kết model luôn khả dụng cho mọi tài khoản. CLI hiện chưa có cờ `--model`; muốn đổi model, chỉnh cấu hình trong [gemini.py](app/llm/gemini.py) hoặc khởi tạo `GeminiLLM` qua Python API.

Luồng sinh manifest gửi README, Rulebook và thông tin khảo sát được đưa vào prompt tới Gemini. Profiler lược mẫu của một số cột khách hàng/liên hệ theo tên cột; đây không phải cơ chế ẩn danh toàn diện.

## 3. Chuẩn bị dữ liệu đầu vào

Mỗi doanh nghiệp có một thư mục riêng; tên thư mục được dùng làm `business_id`:

```text
my_dataset/
├── rulebook.md
└── data/
    ├── biz_example/
    │   ├── README.md
    │   ├── sales.csv
    │   └── products.xlsx
    └── biz_other/
        ├── README.md
        └── pos.sqlite
```

Điều kiện cho luồng build/migration/audit:

1. Mỗi thư mục doanh nghiệp có đúng một file ở cấp thư mục đó có tên bắt đầu bằng `README`, không phân biệt hoa thường.
2. README mô tả ý nghĩa cột, cách ghi dữ liệu và quy ước riêng của doanh nghiệp.
3. `rulebook.md` nằm ở **thư mục cha của `--dataset-dir`**. Ví dụ `--dataset-dir my_dataset/data` sẽ đọc `my_dataset/rulebook.md`.
4. File dữ liệu có thể nằm trong thư mục con; mapping dùng đường dẫn tương đối từ thư mục doanh nghiệp.
5. `business_id` trong manifest phải khớp tên thư mục doanh nghiệp khi ingestion.

| Định dạng | Phần mở rộng | Đối tượng được đọc |
| --- | --- | --- |
| CSV | `.csv` | Một bảng; tên đối tượng là stem của file |
| Excel | `.xls`, `.xlsx`, `.xlsm` | Từng sheet có dữ liệu |
| SQLite | `.sqlite`, `.sqlite3`, `.db` | Từng user table, không tự đọc view |
| Parquet | `.parquet` | Bảng do adapter Parquet trả về |

CSV hiện được mở bằng `utf-8-sig`; code không tự dò mọi encoding. Delimiter được dò trong nhóm dấu phẩy, chấm phẩy, tab và `|`, với fallback CSV mặc định.

## 4. Chạy nhanh với manifest có sẵn

Repo đã có manifest tại `dataset/train/manifests`. Để kiểm tra và nạp một doanh nghiệp, không cần gọi Gemini.

### Bước 1 — Audit manifest với dữ liệu hiện tại

```bash
python -m app.ingestion.audit_manifests \
  --business-id biz_123 \
  --output-dir data/audit/biz_123
```

Mở `data/audit/biz_123/biz_123.json`, đọc `errors`, `coverage` và `unresolved`.

### Bước 2 — Nạp dữ liệu vào DuckDB

```bash
python -m app.ingestion.pipeline \
  --business-dir dataset/train/data/biz_123 \
  --manifest dataset/train/manifests/biz_123.json \
  --output-dir data/cache/biz_123/current
```

### Bước 3 — Xem kết quả

```text
data/cache/biz_123/current/
├── business.duckdb
├── catalog.json
└── manifest.json
```

`manifest.json` là bản sao manifest đã sử dụng. Đọc `catalog.json` để biết tên bảng/cột vật lý, quan hệ, coverage và những điểm cần kiểm tra thêm trước khi viết SQL.

## 5. Luồng xử lý bên trong

### Bước 1 — Khám phá và đọc nguồn

`SourceInventory.discover()` tìm file theo phần mở rộng được hỗ trợ. `SourceInventory.read()` chọn adapter tương ứng. Profiler và ingestion dùng chung điểm dispatch này để tránh hai cách đọc nguồn khác nhau.

Các adapter trả `SourceTable`, bao gồm file, sheet/bảng, tên cột, iterator dữ liệu, vị trí/trạng thái header, kiểu khai báo nếu có và FK SQLite nếu có.

Với CSV/Excel, `TabularLayout` khảo sát tối đa 50 dòng không rỗng ban đầu để chọn header. Khi ingestion có manifest, tên cột trong manifest được dùng làm gợi ý. Nếu không khớp, adapter dùng heuristic và catalog ghi lại trạng thái cần rà soát. SQLite và Parquet lấy schema từ nguồn.

### Bước 2 — Khảo sát mọi đối tượng

`SourceInventory.inspect()` thực hiện:

- Tính SHA-256 cho file nguồn.
- Đọc tất cả đối tượng mà adapter trả về.
- Quét toàn bộ dòng để đếm dòng, null và giá trị số âm thuộc kiểu số Python.
- Ghi kiểu giá trị đã quan sát.
- Giữ tối đa 5 giá trị mẫu/cột; chuỗi mẫu được cắt tối đa 160 ký tự.
- Theo dõi tối đa 1.000 giá trị phân biệt theo biểu diễn chuỗi. Khi vượt giới hạn, `distinct_count` là `null`, `distinct_capped` là `true`, đồng thời giữ `distinct_lower_bound`.

Mẫu có seed cố định để tái lập trên cùng nguồn và thứ tự đọc. Mẫu không chứng minh uniqueness; chuỗi CSV `"-1"` cũng không tự được tính như số âm ở bước profiling này.

### Bước 3 — Tạo evidence bundle

`BusinessManifestService.build_input()` kết hợp:

- `business_id`, `file_inventory` và `source_inventory`.
- Các đoạn README có `evidence_id`.
- `survey_results` từ khảo sát nguồn.
- Toàn bộ phần Rulebook được parser giữ lại.
- Schema đầu ra và tập evidence ID phục vụ kiểm tra nội bộ.

Mỗi đối tượng có evidence ID dạng `profile:<source_file>::<source_object>`. `Rulebook.parse()` giữ phần mở đầu dưới ID `rulebook.preamble`, tách các mục `## N.` và giữ nguyên tiểu mục ngành bên trong. Quy tắc `as_of_date` vì vậy không bị mất khi tách mục.

### Bước 4 — Tạo hoặc nâng cấp manifest

Có hai đường độc lập:

- **Gemini:** tạo manifest từ evidence và schema; kiểm tra locally, thử sửa tối đa 3 lần khi phản hồi sai JSON/schema/tham chiếu được kiểm tra.
- **Offline:** mở rộng mapping cũ sang từng bảng/sheet thực tế, giữ điều chưa rõ là `unresolved`, bổ sung định nghĩa README rõ ràng và overlay đã rà soát.

Offline không có khả năng diễn giải tự do như LLM. Việc kế thừa mapping cũ cũng không chứng nhận lại toàn bộ nghĩa nghiệp vụ của mapping đó.

### Bước 5 — Kiểm tra cấu trúc và ngữ nghĩa

JSON Schema kiểm tra hình thức. `ManifestValidator.validate()` bổ sung kiểm tra đối chiếu nguồn:

- Đúng file, bảng/sheet, cột và vị trí header đã quan sát.
- Không trùng binding bảng/cột hoặc relationship ID.
- Mapping `documented` có semantic name, mô tả và evidence.
- Evidence ID hợp lệ khi caller truyền `evidence_ids`.
- Endpoint quan hệ tồn tại, hai phía có số cột khóa tương ứng.
- Thuộc tính ngữ nghĩa không mâu thuẫn với một số quy tắc đã mã hóa, ví dụ không cộng `stock_balance` qua thời gian hoặc dùng đơn vị cơ bản cho `unit_cost` vốn khai báo theo đơn vị bán.
- Tính coverage và danh sách cột chưa được mapping từ inventory, không chỉ tin danh sách `unresolved` LLM trả về.

Ingestion kiểm tra lại binding theo các đối tượng thực sự được nạp, nhưng không tải evidence bundle để kiểm tra lại mọi ID. Vì vậy nên chạy audit trước ingestion.

### Bước 6 — Chuẩn hóa và nạp dữ liệu

`IngestionPipeline` thực hiện tuần tự:

1. Đọc manifest nếu được cung cấp; kiểm tra schema và `business_id`.
2. Tạo DuckDB tạm và mở transaction.
3. Đọc từng bảng/sheet riêng biệt.
4. Chuẩn hóa tên vật lý bằng `IdentifierNormalizer`.
5. Dùng tối đa 1.000 dòng đầu để suy luận kiểu lưu trữ qua `ValueNormalizer`.
6. `_TableWriter` chuyển giá trị, ghi batch tối đa 1.000 dòng và đếm null/dòng.
7. `CatalogBuilder` gắn semantics được tài liệu hóa vào cột vật lý.
8. Kiểm tra manifest và quan hệ trên dữ liệu đã nạp.
9. Commit DuckDB, xây `quality`, validate catalog, rồi thay thế file đầu ra.

Các kiểu lưu trữ hỗ trợ: `BIGINT`, `DOUBLE`, `BOOLEAN`, `DATE`, `TIMESTAMP`, `VARCHAR`. Trường được nhận diện là mã được ưu tiên lưu text để giữ số 0 đầu. Text số chỉ được chuyển khi định dạng phù hợp các quy tắc hiện có; không tự suy đoán mọi locale.

### Bước 7 — Xuất artifact

`JsonStore.write()` ghi từng JSON qua file tạm trong cùng thư mục rồi `os.replace()`. DuckDB cũng được xây ở file tạm trước khi thay thế file đích.

Tính atomic này áp dụng **từng file**, không phải một transaction chung cho toàn bộ DuckDB, manifest, evidence và report. Nếu có lỗi I/O khi xuất kết quả, cần kiểm tra bộ artifact và chạy lại cùng input.

## 6. Tạo và cập nhật manifest

### 6.1. Nâng cấp offline sang thư mục riêng

Dùng khi đã có manifest v1/v2 và muốn đối chiếu lại nguồn, bổ sung coverage hoặc áp dụng overlay mà không gọi LLM:

```bash
python -m app.ingestion.upgrade_manifests \
  --business-id biz_123 \
  --manifest-dir dataset/train/manifests \
  --output-dir data/manifests_v2
```

Chạy toàn bộ doanh nghiệp bằng cách bỏ `--business-id`:

```bash
python -m app.ingestion.upgrade_manifests \
  --manifest-dir dataset/train/manifests \
  --output-dir data/manifests_v2
```

Sau khi review, có thể cập nhật trực tiếp thư mục manifest hiện có:

```bash
python -m app.ingestion.upgrade_manifests \
  --output-dir dataset/train/manifests
```

Lệnh migration **ghi đè file đích tương ứng** và không có cờ `--overwrite`. Nếu muốn giữ bản cũ để so sánh, dùng thư mục đầu ra riêng như ví dụ đầu.

| Tham số | Mặc định / ý nghĩa |
| --- | --- |
| `--dataset-dir` | `dataset/train/data` |
| `--manifest-dir` | `dataset/train/manifests`; nơi đọc manifest hiện có |
| `--output-dir` | Bắt buộc; nơi ghi manifest, evidence và report mới |
| `--overlays-dir` | `dataset/train/manifest_overrides` |
| `--business-id` | Tùy chọn; bỏ trống để xử lý các thư mục doanh nghiệp |

`ManifestMigrator` chỉ tự bổ sung định nghĩa README literal dạng cột “là” một ý nghĩa khi xác định được binding duy nhất. Tên cột lặp ở nhiều bảng mà không đủ phạm vi sẽ không được tự chọn một cách tùy ý.

### 6.2. Sinh mới bằng Gemini

Dùng khi cần LLM diễn giải README và evidence để đề xuất manifest mới:

```bash
python -m app.ingestion.build_manifests \
  --business-id biz_123 \
  --output-dir data/manifests_generated
```

Nếu file đích đã tồn tại, mặc định lệnh bỏ qua doanh nghiệp đó. Muốn sinh lại:

```bash
python -m app.ingestion.build_manifests \
  --business-id biz_123 \
  --output-dir data/manifests_generated \
  --overwrite
```

| Tham số | Mặc định / ý nghĩa |
| --- | --- |
| `--dataset-dir` | `dataset/train/data` tính từ root repo |
| `--output-dir` | `dataset/train/manifests` tính từ root repo |
| `--schema` | `schemas/business_manifest.v2.schema.json` |
| `--business-id` | Tùy chọn; chỉ sinh cho doanh nghiệp này |
| `--overwrite` | Cho phép sinh lại file manifest đã có |

CLI dùng khoảng cách tối thiểu 4,25 giây giữa các lần bắt đầu gọi Gemini. Vòng thử lại tối đa 3 lần thuộc bước sửa phản hồi không hợp lệ; không phải cơ chế retry tổng quát cho mọi lỗi mạng/quota.

### 6.3. Áp dụng chỉnh sửa đã rà soát bằng overlay

Overlay nằm tại `dataset/train/manifest_overrides/<business_id>.json`. Nó có thể cập nhật vai trò bảng, `grain`, metadata cột đã tồn tại và bổ sung quan hệ.

Ví dụ phần chỉnh sửa một cột:

```json
{
  "tables": [
    {
      "source_file": "pos.sqlite",
      "source_object": "don_vi_sp",
      "table_role": "unit_conversions",
      "columns": [
        {
          "source_name": "HeSoQuyDoi",
          "semantic_name": "unit_conversion_factor",
          "description": "Số đơn vị cơ bản trong một đơn vị của dòng",
          "status": "documented",
          "source_refs": ["readme.conventions", "profile:pos.sqlite::don_vi_sp"],
          "unit_basis": "base_unit",
          "time_role": null,
          "aggregation": "none"
        }
      ]
    }
  ],
  "relationships": []
}
```

Overlay không tạo thêm cột vật lý; tên bảng/cột phải có trong manifest được mở rộng từ nguồn. Xem bản thực tế tại [biz_123.json](dataset/train/manifest_overrides/biz_123.json).

**Build bằng Gemini không tự áp dụng overlay.** Nếu cần áp overlay lên kết quả vừa sinh, chạy migration với thư mục đó làm input:

```bash
python -m app.ingestion.upgrade_manifests \
  --business-id biz_123 \
  --manifest-dir data/manifests_generated \
  --output-dir data/manifests_reviewed
```

## 7. Audit trước ingestion

Audit đọc lại dữ liệu nguồn hiện tại, validate manifest và viết báo cáo; không gọi Gemini, không tạo DuckDB và không sửa manifest:

```bash
python -m app.ingestion.audit_manifests \
  --dataset-dir dataset/train/data \
  --manifest-dir data/manifests_reviewed \
  --business-id biz_123 \
  --output-dir data/audit/reviewed
```

| Tham số | Mặc định / ý nghĩa |
| --- | --- |
| `--dataset-dir` | `dataset/train/data` |
| `--manifest-dir` | `dataset/train/manifests` |
| `--output-dir` | Bắt buộc; mỗi doanh nghiệp có một report JSON |
| `--business-id` | Tùy chọn |

Cách đọc report:

| Trường | Ý nghĩa |
| --- | --- |
| `errors` | Binding/evidence/ngữ nghĩa có lỗi phải sửa |
| `unresolved` | Chưa đủ mô tả hoặc header cần xác nhận; không nhất thiết là lỗi cấu trúc |
| `coverage.source_objects` | Số bảng/sheet nguồn quan sát được |
| `coverage.manifest_objects` | Số đối tượng được khai báo trong manifest v2 |
| `coverage.source_columns` | Tổng số cột nguồn |
| `coverage.documented_columns` | Số cột có mapping được tài liệu hóa |
| `coverage.unresolved_columns` | Số cột chưa có mapping được tài liệu hóa |

CLI trả mã thoát `1` nếu báo cáo có `errors`, `0` nếu không có loại lỗi đó; lỗi đọc file/schema có thể làm lệnh dừng với exception. `unresolved` còn tồn tại không tự làm CLI thất bại. Audit không thay thế bước kiểm tra full-data relationships khi ingestion.

## 8. Nạp DuckDB và xây catalog

```bash
python -m app.ingestion.pipeline \
  --business-dir dataset/train/data/biz_123 \
  --manifest data/manifests_reviewed/biz_123.json \
  --output-dir data/cache/biz_123/reviewed
```

| Tham số | Ý nghĩa |
| --- | --- |
| `--business-dir` | Bắt buộc; thư mục một doanh nghiệp |
| `--output-dir` | Bắt buộc; thư mục output DuckDB/catalog |
| `--manifest` | Tùy chọn; nếu bỏ qua thì không có mapping nghiệp vụ từ manifest |

Nếu bỏ `--manifest`, dữ liệu vẫn được nạp khi nguồn hợp lệ, nhưng catalog ghi vấn đề thiếu manifest. Không nên coi catalog đó là đã sẵn sàng cho SQL nghiệp vụ.

Chạy lại cùng `--output-dir` sẽ xây database mới và thay thế đầu ra sau khi hoàn thành các kiểm tra tương ứng. Đây không phải incremental ingestion hay cache được tự chọn bằng hash.

Ví dụ kiểm tra catalog và database đã tạo:

```bash
python - <<'PY'
import json
from pathlib import Path
import duckdb

output = Path("data/cache/biz_123/current")
catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
print("Tables:", len(catalog["tables"]))
print("Unresolved:", len(catalog["unresolved"]))
print(json.dumps(catalog["quality"], ensure_ascii=False, indent=2))

with duckdb.connect(str(output / "business.duckdb"), read_only=True) as connection:
    print(connection.execute("SHOW TABLES").fetchall())
PY
```

Khi viết SQL, lấy tên từ `physical_name` trong catalog; không dùng `semantic_name` làm tên cột DuckDB.

## 9. Cấu trúc manifest, evidence và catalog

### 9.1. BusinessManifest v2

| Trường | Nội dung |
| --- | --- |
| `schema_version` | `"2.0"` |
| `business_id` | Tên doanh nghiệp/thư mục |
| `tables` | Mỗi entry gắn với chính xác một file và một bảng/sheet |
| `relationships` | Endpoint nguồn/đích, khóa ghép, cardinality, nguồn chứng cứ |
| `facts` | Các quy ước, giới hạn dữ liệu và mô tả có evidence |
| `rule_refs` | Các Rulebook ID áp dụng |
| `unresolved` | Vấn đề chưa đủ căn cứ, lý do và evidence cần bổ sung |

Một bảng có `source_file`, `source_object`, `table_role`, `grain`, `header_row_number`, `columns`, `source_refs`. Một cột có:

| Metadata cột | Mục đích |
| --- | --- |
| `source_name` | Tên đúng trong nguồn |
| `semantic_name`, `description` | Khái niệm và diễn giải nghiệp vụ |
| `status` | `documented`, `inferred_candidate` hoặc `unresolved` |
| `source_refs` | Bằng chứng cho mapping |
| `unit_basis` | Đơn vị bán/nhập/cơ bản, tiền tệ hoặc chưa rõ |
| `time_role` | Phân biệt ngày đặt, ngày thu, thời điểm biến động… |
| `aggregation` | `sum`, `last`, `weighted_average`, `none` hoặc `unknown` |

Chỉ cột `documented` được đưa vào semantics đáng tin cậy của catalog v2. Candidate có thể hữu ích cho review nhưng chưa được coi là mapping đã xác nhận. Các trường semantics chưa rõ có thể là `null` theo schema.

Manifest v1 vẫn được chấp nhận để tương thích, nhưng binding sai nguồn từng bị bỏ qua trước đây có thể khiến ingestion hiện tại báo lỗi. Nên migration sang v2 trước khi tiếp tục bổ sung mapping.

### 9.2. Evidence và report

Build/migration ghi:

```text
<output-dir>/
├── biz_123.json
├── evidence/
│   └── biz_123.json
└── reports/
    └── biz_123.json
```

Evidence bundle chứa README sections, Rulebook sections, survey results, inventory và fingerprint file nguồn. Đây là dữ liệu để truy nguyên mapping, không phải database dùng truy vấn nghiệp vụ.

SHA-256 được ghi để đối chiếu snapshot; code chưa tự từ chối mọi artifact cũ chỉ dựa trên hash. Sau khi thay đổi file nguồn, cần chạy lại audit và các bước tạo artifact liên quan.

`reports/summary.json` có trong repo là snapshot tổng hợp của lần kiểm tra trước, **không được các CLI hiện tại tự cập nhật**. Các CLI ghi report theo từng doanh nghiệp.

### 9.3. Catalog

Catalog chứa tên nguồn, tên vật lý, semantics, kiểu lưu trữ, alias, row/null counts, source mappings, facts, rule references, relationships, `quality` và `unresolved`.

`unresolved` của catalog có thể nhiều hơn hoặc khác report manifest vì catalog còn tổng hợp vấn đề từ ingestion, lựa chọn header và kiểm tra quan hệ. Không cộng số issue rồi coi đó là số lỗi nghiệp vụ độc lập.

Kiểm tra riêng JSON Schema của catalog:

```bash
python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator

schema = json.loads(Path("schemas/catalog.schema.json").read_text(encoding="utf-8"))
catalog = json.loads(
    Path("data/cache/biz_123/current/catalog.json").read_text(encoding="utf-8")
)
Draft202012Validator(schema).validate(catalog)
print("Catalog schema: OK")
PY
```

## 10. Relationships và chất lượng nghiệp vụ

### 10.1. Xác minh quan hệ

Quan hệ manifest hỗ trợ `many_to_one` và `one_to_one`, cùng khóa ghép. `RelationshipService.from_manifest()` kiểm tra trên toàn bộ dữ liệu đã nạp:

- Null ở khóa nguồn và đích.
- Khóa đích bị trùng.
- Khóa nguồn bị trùng; đây có thể là điều bình thường với `many_to_one`.
- Dòng nguồn có khóa đầy đủ nhưng không tìm được bản ghi đích.
- Ràng buộc uniqueness bổ sung cho `one_to_one`.

| Trạng thái | Ý nghĩa |
| --- | --- |
| `declared_not_data_verified` | FK SQLite được adapter ghi nhận nhưng entry này chưa qua kiểm tra dữ liệu |
| `verified` | Quan hệ có khai báo bằng chứng, không phải candidate, và vượt qua kiểm tra dữ liệu hiện có |
| `candidate` | Quan hệ còn là suy luận, dù giá trị khóa có thể khớp |
| `failed_validation` | Quan hệ không vượt qua kiểm tra dữ liệu |

Việc kiểm tra evidence ID không chứng minh tự động rằng nội dung tài liệu thực sự suy ra mọi quan hệ. Khi thêm quan hệ, vẫn phải review ý nghĩa và phạm vi khóa.

`RelationshipService.verified(catalog)` chỉ trả các entry `verified`. `RelationshipService.validate_join()` từ chối quan hệ chưa xác minh và chặn trường hợp cộng số đo phía “một” sau khi join mở rộng sang phía “nhiều”, khi caller khai báo `aggregate_from` tương ứng. Đây là helper cho code gọi, chưa phải bộ phân tích tự động mọi câu SQL.

### 10.2. Tránh nhân số liệu khi join

Ví dụ một đơn có nhiều dòng hàng và nhiều lần thu tiền. Join trực tiếp cả ba bảng rồi cộng có thể nhân doanh thu hoặc số tiền thu. Cần tổng hợp từng nhánh về cấp đơn trước khi nối; một khóa join `verified` không tự giải quyết vấn đề cấp dữ liệu này.

### 10.3. Đánh giá capability

`Rulebook.assess_capabilities()` kiểm tra sự hiện diện của semantic names và rule references cho:

| Capability | Nghiệp vụ |
| --- | --- |
| `revenue` | Doanh thu |
| `receivables` | Công nợ |
| `inventory_as_of` | Tồn tại thời điểm |
| `gross_profit` | Lãi gộp |

Kết quả:

- `blocked`: thiếu semantics hoặc Rulebook references cần thiết.
- `partial`: có các tên semantics/rule references cần thiết, nhưng còn phải kiểm tra grain, đường join, trạng thái/trả hàng, thời gian và đơn vị.

Hiện không tự xuất trạng thái `ready`. Kiểm tra này không khẳng định mọi cột cần thiết nằm trên một đường join hợp lệ hoặc các mã trạng thái đã được giải nghĩa đầy đủ.

## 11. Sử dụng API class

Các ví dụ chạy từ root repo, không cần CLI wrapper.

### Khảo sát và kiểm tra manifest

```python
import json
from pathlib import Path
from jsonschema import Draft202012Validator

from app.ingestion.source_inventory import SourceInventory
from app.manifests.validation import ManifestValidator

business_dir = Path("dataset/train/data/biz_123")
manifest = json.loads(
    Path("dataset/train/manifests/biz_123.json").read_text(encoding="utf-8")
)
schema = json.loads(
    Path("schemas/business_manifest.schema.json").read_text(encoding="utf-8")
)
Draft202012Validator(schema).validate(manifest)
inventory = SourceInventory.inspect(business_dir)
report = ManifestValidator.validate(manifest, inventory)
print(report["coverage"])
print(report["errors"])
```

Ví dụ này kiểm tra nguồn, không kiểm tra evidence ID vì không truyền `evidence_ids`. Muốn kiểm tra đầy đủ evidence, dùng audit CLI hoặc xây business input rồi truyền `business_input["_evidence_ids"]`.

### Sinh manifest bằng service

```python
import json
from pathlib import Path

from app.llm.gemini import GeminiLLM
from app.manifests.service import BusinessManifestService
from app.storage.json_store import JsonStore

schema = json.loads(
    Path("schemas/business_manifest.v2.schema.json").read_text(encoding="utf-8")
)
rulebook = Path("dataset/train/rulebook.md").read_text(encoding="utf-8")
business_input = BusinessManifestService.build_input(
    Path("dataset/train/data/biz_123"), rulebook, schema
)
llm = GeminiLLM.from_env(min_request_interval_seconds=4.25)
manifest = BusinessManifestService.generate(llm, business_input, schema)
JsonStore.write(manifest, Path("data/generated/biz_123.json"))
JsonStore.write(
    BusinessManifestService.build_evidence(business_input),
    Path("data/generated/evidence/biz_123.json"),
)
```

`BusinessManifestService.generate()` trả object; việc lưu artifact do caller thực hiện. CLI build bổ sung bước lưu report so với ví dụ ngắn này.

### Ingestion và lấy quan hệ đã xác minh

```python
from pathlib import Path

from app.catalog.relationships import RelationshipService
from app.ingestion.pipeline import IngestionPipeline

catalog = IngestionPipeline.ingest(
    business_dir=Path("dataset/train/data/biz_123"),
    output_dir=Path("data/cache/biz_123/current"),
    manifest_path=Path("dataset/train/manifests/biz_123.json"),
)
relationships = RelationshipService.verified(catalog)
print(relationships)
```

## 12. Kiểm thử và kiểm chứng

Chạy toàn bộ test:

```bash
python -m pytest -q
```

Hoặc chạy riêng các nhóm:

```bash
python -m pytest tests/test_architecture.py -q
python -m pytest tests/test_manifest_v2.py tests/test_ingestion.py -q
```

| File test | Phạm vi |
| --- | --- |
| `test_architecture.py` | Hàm thuộc class, import module, CLI help và migration → audit → ingestion |
| `test_business_manifest.py` | Evidence, Rulebook, JSON/schema validation và sửa phản hồi |
| `test_manifest_v2.py` | Binding chính xác, khóa ghép, orphan, candidate join, semantics và README enrichment |
| `test_ingestion.py` | CSV/Excel/SQLite/Parquet, kiểu dữ liệu, mã giữ số 0 đầu và catalog |
| `test_gemini.py` | Cấu hình key/model và xử lý phản hồi client bằng mock |

Lần kiểm tra sau refactor class đạt **36 test passed**. Test Gemini dùng mock, không chứng minh API/model thực tế đang hoạt động và không cần key thật.

Snapshot coverage đang lưu tại [reports/summary.json](dataset/train/manifests/reports/summary.json):

| Chỉ số | Giá trị snapshot |
| --- | ---: |
| Doanh nghiệp | 30 |
| Bảng/sheet | 339 |
| Cột nguồn | 3.184 |
| Cột được mapping từ tài liệu | 2.415 |
| Cột còn unresolved | 769 |
| Lỗi đối chiếu nguồn/schema trong snapshot | 0 |

Trường `tests` trong snapshot ghi `29 passed` từ lần kiểm tra trước refactor; nó không phải kết quả test mới nhất. Các số coverage là snapshot, cần audit lại nếu dữ liệu hoặc manifest thay đổi.

Ingestion thực tế đã được kiểm tra trước đó với `biz_101` (CSV), `biz_105` (Excel) và `biz_123` (SQLite). Riêng `biz_123`:

- 17 bảng, 157 cột; 16 cột có mapping và 141 cột chưa đủ mô tả nghiệp vụ.
- Sửa nhầm tên bảng `don_vi_sp` thành mapping cột `don_vi_sp.HeSoQuyDoi`.
- Tách vai trò đơn hàng, dòng bán, thanh toán và biến động kho.
- Quan hệ chi tiết đơn → đơn hàng dùng khóa ghép `IDCuaHang, MaHoaDon` đã vượt qua kiểm tra dữ liệu ở lần ingestion đó.

## 13. Quy trình mở rộng repo

### Thêm doanh nghiệp

1. Tạo `dataset/train/data/<business_id>/` và đặt file nguồn cùng một README.
2. Ghi rõ phạm vi mỗi dòng, đơn vị và ý nghĩa các cột tiền/ngày/trạng thái trong README.
3. Dùng `SourceInventory.inspect()` kiểm tra tên bảng, header và cột thực tế.
4. Sinh manifest v2 bằng build CLI; hoặc dùng migration nếu đã có manifest tương ứng.
5. Rà soát mapping candidate/unresolved; bổ sung tài liệu hoặc overlay khi có căn cứ.
6. Chạy audit với đúng thư mục manifest vừa tạo.
7. Chạy ingestion vào một output riêng và kiểm tra `relationships`, `quality`, `unresolved`.
8. Thêm test hồi quy nếu dữ liệu mới phát hiện một quy tắc hoặc layout chưa được hỗ trợ.

### Thêm định dạng nguồn

1. Tạo class adapter trong `app/ingestion/adapters`.
2. Trả `SourceTable` với provenance, headers và iterator hàng nhất quán.
3. Đăng ký phần mở rộng và dispatch tại `SourceInventory`.
4. Viết test có nguồn mẫu, kiểm tra đọc đúng bảng/cột/dòng và đóng tài nguyên.
5. Kiểm tra cả profiling lẫn ingestion vì hai luồng dùng chung adapter.

### Thêm semantic hoặc luật

1. Kiểm tra semantic mới có được hỗ trợ bởi README/Rulebook hay không.
2. Cập nhật prompt, schema nếu cần mở rộng enum và logic migration nếu có định nghĩa tương ứng.
3. Bổ sung ràng buộc tại `ManifestValidator.validate_column_semantics()` khi có điều kiện có thể kiểm tra bằng code.
4. Nếu là capability mới, cập nhật `REQUIREMENTS` trong `app/rules/rulebook.py` và schema của `quality` trong catalog.
5. Thêm test cho cả trường hợp hợp lệ và trường hợp dễ nhầm nghiệp vụ.

Giữ mọi implementation dưới dạng class method. Không thêm lại hàm độc lập hoặc module wrapper chứa logic trùng với service hiện có.

## 14. Xử lý lỗi thường gặp

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| `GEMINI_APIKEY is missing or empty` | Chưa cấu hình key cho bước build | Đặt `GEMINI_APIKEY` trong `.env` tại root; offline migration/audit/ingestion không cần key |
| Lỗi API/model/quota | Client hoặc tài khoản không thực hiện được request | Kiểm tra thông báo API và cấu hình `GeminiLLM`; vòng sửa JSON không tự xử lý mọi lỗi mạng |
| `Skipping existing manifest` | File output đã tồn tại | Dùng thư mục output khác hoặc thêm `--overwrite` cho build |
| `Expected exactly one README` | Thiếu README hoặc có nhiều file khớp | Giữ đúng một README ở cấp thư mục doanh nghiệp |
| Không tìm thấy Rulebook | Sai quan hệ giữa dataset-dir và rulebook | Đặt `rulebook.md` ở thư mục cha của `--dataset-dir` |
| `business_id` không khớp | Dùng manifest của thư mục khác | Đối chiếu tên thư mục và `business_id` |
| `Unknown source column/object` | Tên cột/bảng/file không có thật | Đọc inventory; sửa đúng source identifier, không dùng alias/semantic name thay tên nguồn |
| `Unknown ... evidence` | ID không nằm trong tập evidence đang kiểm tra | Xây lại evidence hoặc sửa source_refs; khảo sát v1 lưu trữ cũ có thể cần review khi audit bằng evidence mới |
| `Header disagrees with observed source` | Header thay đổi hoặc được chọn sai | Kiểm tra worksheet/CSV và bước chọn header; xác nhận layout trước khi sửa manifest |
| Lỗi decode CSV | File không phải UTF-8 | Xác nhận encoding; chuyển bản nguồn phù hợp hoặc bổ sung adapter hỗ trợ encoding rõ ràng |
| Lỗi chuyển kiểu khi nạp | Dòng sau mẫu suy luận kiểu có giá trị khác loại | Kiểm tra cột và mở rộng quy tắc chuẩn hóa/suy luận kiểu có test; không ép mất dữ liệu |
| `failed_validation` ở relationship | Trùng khóa đích, orphan hoặc cardinality sai | Kiểm tra khóa ghép, phạm vi cửa hàng/chi nhánh và ý nghĩa join |
| Audit không có errors nhưng còn unresolved | Cấu trúc hợp lệ, nghĩa chưa đủ | Bổ sung bằng chứng; không chuyển mặc định thành số 0 hoặc giả định semantics |
| Không import được hàm cũ | Repo đã chuyển sang API class | Dùng class trong `app/manifests`, `app/catalog`, `app/ingestion` theo các ví dụ phía trên |

## 15. Giới hạn hiện tại

- Header CSV/Excel dùng heuristic khi không có đối chiếu phù hợp; chưa có xử lý tổng quát mọi header nhiều tầng, ô gộp hoặc dòng tổng lặp.
- Ingestion không tự forward-fill nhóm đơn, loại dòng trùng, dựng lại năm còn thiếu hoặc thay đổi dữ liệu thô theo toàn bộ Rulebook.
- Suy luận kiểu lưu trữ dựa trên mẫu ban đầu; dữ liệu khác kiểu ở phần sau có thể làm ingestion thất bại.
- Thu thập Rulebook và khai báo metadata không đồng nghĩa đã thực thi toàn bộ luật doanh thu, trả hàng, công nợ, tồn kho và giá vốn trong SQL.
- `documented` là trạng thái mapping có mô tả/evidence, không phải kết quả kiểm chứng tự động mọi ý nghĩa kinh doanh.
- Audit hiện kiểm tra cấu trúc và evidence, còn full-data relationship checks nằm ở ingestion.
- Guard join chưa phân tích mọi dạng SQL, fan-out nhiều nhánh hay mọi tác động của null khóa.
- Chưa có cơ chế cache tự động theo fingerprint, incremental ingestion, API server hoặc pipeline hỏi đáp hoàn chỉnh.
- Mỗi JSON/database được thay thế riêng; chưa có transaction xuất artifact nhiều file.

Tài liệu bổ sung: [kiến trúc và API](docs/architecture.md), [chi tiết manifest v2](docs/manifest-v2.md), [Rulebook tập train](dataset/train/rulebook.md).
