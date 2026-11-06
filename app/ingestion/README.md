# Ingestion

Các module được nhóm theo chức năng:

```text
ingestion/
├── datasets/                 # Dữ liệu dùng trực tiếp bởi agent, training, evaluation
│   ├── loader.py             # Business, Split, đọc bảng và nạp split
│   ├── profiling.py          # Mô tả bảng cho prompt, gộp sheet cùng schema
│   └── splitting.py          # Chia business thành fit/holdout
├── sources/                  # Đọc và kiểm kê dữ liệu nguồn
│   ├── inventory.py          # Phát hiện file, thống kê và tạo evidence
│   └── adapters/             # Bộ đọc theo định dạng
│       ├── common.py         # SourceTable, TabularLayout
│       ├── csv_adapter.py
│       ├── excel_adapter.py
│       ├── parquet_adapter.py
│       └── sqlite_adapter.py
├── processing/               # Chuẩn hóa và lưu dữ liệu phục vụ truy vấn
│   ├── normalization.py      # Chuẩn hóa tên cột, suy luận kiểu, chuyển giá trị
│   └── pipeline.py           # Nạp DuckDB và tạo catalog
└── cli/                      # Điểm vào dòng lệnh
    ├── ingest.py
    ├── build_manifests.py
    ├── audit_manifests.py
    └── upgrade_manifests.py
```

Luồng agent dùng `datasets.loader` để nạp DataFrame và `datasets.profiling`
để tạo mô tả bảng. Training/evaluation dùng thêm `datasets.splitting`.

Luồng DuckDB dùng `sources.inventory` và các adapter để đọc nguồn, sau đó
`processing.pipeline` chuẩn hóa dữ liệu, ghi DuckDB và tạo catalog.
Các lệnh manifest gọi phần triển khai trong `app.manifests`.

Ví dụ import:

```python
from app.ingestion.datasets.loader import Business, Split, load_split
from app.ingestion.datasets.profiling import profile_text
from app.ingestion.datasets.splitting import holdout_businesses
from app.ingestion.sources.inventory import SourceInventory
from app.ingestion.processing.pipeline import IngestionPipeline
```

Các lệnh CLI:

```bash
python -m app.ingestion.cli.ingest --help
python -m app.ingestion.cli.build_manifests --help
python -m app.ingestion.cli.audit_manifests --help
python -m app.ingestion.cli.upgrade_manifests --help
```

Bốn file `pipeline.py`, `build_manifests.py`, `audit_manifests.py` và
`upgrade_manifests.py` ở gốc package chỉ chuyển tiếp sang CLI mới để giữ
tương thích với các lệnh `python -m app.ingestion.<tên cũ>`.
Các import Python khác dùng đường dẫn mới như ví dụ trên.
