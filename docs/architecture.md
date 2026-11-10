# Repository structure

Application operations are class methods. Pure transformations use static
methods; `GeminiLLM.from_env()` is a class factory, and the internal table writer
owns its connection, normalization configuration and counters. There are no free
functions, including nested helpers. Tests follow the same ownership convention.

| Package | Responsibility | Main classes |
| --- | --- | --- |
| `app/ingestion/datasets` | Load splits and DataFrames, describe tables for agent prompts and select holdout businesses | `Business`, `Split` |
| `app/ingestion/sources/adapters` | Dispatch CSV/Excel/SQLite/Parquet to the loss-aware raw readers | `CsvAdapter`, `ExcelAdapter`, `SqliteAdapter`, `ParquetAdapter` |
| `app/ingestion/sources` | Discover/profile source files and emit evidence | `SourceInventory` |
| `app/ingestion` | Read Raw, type once, publish DuckDB/catalog and expose Typed frames | `IngestionPipeline`, `IngestedBusiness`, `Split` |
| `app/ingestion/organization` | Analyze relationships and build validated semantic views | `analyze_connection`, `organize_connection` |
| `app/ingestion/catalog.py` | Bind layered tables and manifest metadata to the SQL catalog contract | `install_catalog`, `read_catalog` |
| `app/agent/sql_context.py` | Convert catalog and completeness metadata to per-business SQL context | `SQLContextBuilder` |
| `app/ingestion/cli` | Command-line entry points for ingestion and manifest operations | Thin wrappers around processing/manifests APIs |
| `app/manifests` | Evidence, generation, migration, source validation and commands | `BusinessManifestService`, `ManifestMigrator`, `ManifestValidator`, `BuildManifestsCommand`, `AuditManifestsCommand` |
| `app/catalog` | Bind physical columns, verify relationships and guard joins | `CatalogBuilder`, `RelationshipService` |
| `app/rules` | Preserve Rulebook sections and report capability gaps | `Rulebook` |
| `app/storage` | Atomic JSON persistence for manifests, evidence and catalogs | `JsonStore` |
| `app/llm` | Gemini configuration, calls and prompt constants | `GeminiLLM` |

Python API examples:

```python
import json
from pathlib import Path

from app.ingestion.processing.pipeline import IngestionPipeline
from app.ingestion.sources.inventory import SourceInventory
from app.manifests.validation import ManifestValidator
from app.catalog.relationships import RelationshipService

manifest = json.loads(Path("dataset/train/manifests/biz_123.json").read_text())
inventory = SourceInventory.inspect(Path("dataset/train/data/biz_123"))
report = ManifestValidator.validate(manifest, inventory)
catalog = IngestionPipeline.ingest(
    Path("dataset/train/data/biz_123"),
    Path("/tmp/biz_123"),
    Path("dataset/train/manifests/biz_123.json"),
)
relationships = RelationshipService.verified(catalog)
```

Canonical command paths:

```bash
python -m app.ingestion.cli.build_manifests --help
python -m app.ingestion.cli.upgrade_manifests --help
python -m app.ingestion.cli.audit_manifests --help
python -m app.ingestion.cli.ingest --help
```

The manifest commands delegate to `app/manifests`; the ingestion command delegates
to `app/ingestion/processing/pipeline.py`. The original commands
`app.ingestion.build_manifests`, `app.ingestion.upgrade_manifests`,
`app.ingestion.audit_manifests` and `app.ingestion.pipeline` remain as compatibility
entry points. See the [ingestion guide](../app/ingestion/README.md) for the full
layout and updated Python imports.

Ingestion/query integration:

- CSV/Excel/SQLite/Parquet use one reader and type-conversion path for both DuckDB
  ingestion and the pandas fallback.
- `IngestionPipeline` writes Raw/Typed/Semantic, catalog, lineage and guide from
  the same `IngestedBusiness`; the SQL agent consumes the structured catalog.
- Parse failures and source NULLs are distinguished in each column's SQL context.
- Catalog retains the existing SQL contract for physical names, semantic
  mappings, relationships, facts and unresolved items.

`tests/test_architecture.py` checks class ownership, imports and the CLI migration,
audit and ingestion workflow so future file moves cannot silently break them.
