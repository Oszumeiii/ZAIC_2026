# Repository structure

Application operations are class methods. Pure transformations use static
methods; `GeminiLLM.from_env()` is a class factory, and the internal table writer
owns its connection, normalization configuration and counters. There are no free
functions, including nested helpers. Tests follow the same ownership convention.

| Package | Responsibility | Main classes |
| --- | --- | --- |
| `app/ingestion/adapters` | Read each source format and preserve row/header provenance | `SourceTable`, `TabularLayout`, `CsvAdapter`, `ExcelAdapter`, `SqliteAdapter`, `ParquetAdapter` |
| `app/ingestion` | Discover/profile sources, normalize values and load DuckDB | `SourceInventory`, `IdentifierNormalizer`, `ValueNormalizer`, `IngestionPipeline` |
| `app/manifests` | Evidence, generation, migration, source validation and commands | `BusinessManifestService`, `ManifestMigrator`, `ManifestValidator`, `BuildManifestsCommand`, `AuditManifestsCommand` |
| `app/catalog` | Bind physical columns, verify relationships and guard joins | `CatalogBuilder`, `RelationshipService` |
| `app/rules` | Preserve Rulebook sections and report capability gaps | `Rulebook` |
| `app/storage` | Atomic JSON persistence for manifests, evidence and catalogs | `JsonStore` |
| `app/llm` | Gemini configuration, calls and prompt constants | `GeminiLLM` |

Python API examples:

```python
import json
from pathlib import Path

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_inventory import SourceInventory
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

The command paths remain compatible:

```bash
python -m app.ingestion.build_manifests --help
python -m app.ingestion.upgrade_manifests --help
python -m app.ingestion.audit_manifests --help
python -m app.ingestion.pipeline --help
```

The first three modules are thin entry points. Their implementations live in
`app/manifests`; they do not duplicate application logic. Python imports of the
old free functions must be changed to the class APIs above.

Consolidations:

- Discovery and format dispatch share `SourceInventory` across profiling and ingestion.
- Identifier normalization and aliases share one module with value normalization.
- Source and semantic validation share `ManifestValidator`.
- Relationship construction, verification, retrieval and join guards share `RelationshipService`.
- Rulebook parsing and capability requirements share `Rulebook`.
- All JSON writes use `JsonStore`; the unused DuckDB connection wrapper was removed.
- The empty Colab notebook scaffold was removed; source data and manifests are retained.

`tests/test_architecture.py` checks class ownership, imports and the CLI migration,
audit and ingestion workflow so future file moves cannot silently break them.
