# ZAIC DataMate

## Ingest a business dataset

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Ingest one business directory and use its BusinessManifest to map source
columns to documented business meanings:

```bash
python -m app.ingestion.pipeline \
  --business-dir dataset/train/data/biz_130 \
  --manifest dataset/train/manifests/biz_130.json \
  --output-dir data/cache/biz_130/current
```

The pipeline reads CSV, Excel (`.xls`, `.xlsx`, `.xlsm`), SQLite, and Parquet
sources. It writes `business.duckdb`, `catalog.json`, and a copy of the input
manifest into the output directory. SQLite-declared foreign keys are recorded
as `declared_not_data_verified`; the pipeline does not infer joins from
similar column names.

The catalog records the original `source_name`, normalized DuckDB
`physical_name`, documented `semantic_name`, DuckDB type, aliases, source
file/sheet/table, and README evidence references. Use `physical_name` values
when generating SQL. Review the catalog's `unresolved` list before relying on
a mapping.

Header rows are matched to documented manifest column names where possible.
Otherwise the adapter uses a bounded header heuristic and records that choice
as unresolved metadata for review. Each source file, workbook sheet, or SQLite
table is loaded separately; the pipeline does not infer joins or combine
tables. CSV numeric text is converted only when its format is unambiguous;
identifier-like fields are kept as text to preserve leading zeroes.

Validate a generated catalog against `schemas/catalog.schema.json`.
## BusinessManifest v2

Manifest generation now inspects every CSV, Excel sheet, SQLite table and Parquet
object before asking Gemini for semantic mappings. The profiler scans all rows
for counts and keeps bounded deterministic samples; capped distinct counts are
explicitly marked. It does not infer business meaning from a sample. Heuristic
Excel/CSV headers remain review items.

`schemas/business_manifest.schema.json` accepts v1 and v2 for compatibility.
Generation defaults to `schemas/business_manifest.v2.schema.json`. V2 identifies
sources by exact relative file path and sheet/table name, records each column's
evidence, semantic status, unit basis, time role and aggregation, and supports
composite relationships. Only `documented` columns become catalog semantics.
Candidates remain unresolved. Invalid source bindings or contradictory semantics
fail generation (with repair attempts) and ingestion.

Generate a new v2 manifest using the configured Gemini client:

```bash
python -m app.ingestion.build_manifests --business-id biz_123 --overwrite
```

Upgrade existing manifests offline, preserving their documented mappings and
marking uncovered source columns unresolved. This migration does not generate new
business interpretations. Reviewed corrections live in
`dataset/train/manifest_overrides/` and are applied by this command:

```bash
python -m app.ingestion.upgrade_manifests --output-dir dataset/train/manifests
```

Audit against current source files without calling Gemini or creating a database:

```bash
python -m app.ingestion.audit_manifests \
  --business-id biz_123 --output-dir /tmp/zaic-manifest-audit
```

Each generation/upgrade writes an evidence bundle (including source SHA-256
fingerprints) and a coverage report next to the manifests. Full Rulebook text,
including its preamble and nested industry rules, is retained. README conventions
are preserved during offline migration rather than reduced to a date-only fact.

V2 relationship checks run against all ingested rows: composite target-key
uniqueness, null keys and orphan rows. `verified` means a documented relationship
passed those checks; inferred candidates are never promoted just because values
match. `RelationshipService.validate_join()` blocks unverified joins and aggregation
from the one side of a one-to-many join without prior aggregation. Multiple fact
branches still require a query-specific grain review.

Catalog `quality` separates column coverage from business readiness. `blocked`
means required semantic fields/rules are missing; `partial` means those fields
exist but grain, joins, return/status handling and time/unit interpretation still
need query-specific checks. No capability is automatically certified `ready`
from column names alone. The SQL-agent pipeline is a separate capability and is not implemented here.

## Code organization

The Python API is organized into classes under `app/manifests`, `app/catalog`,
`app/ingestion`, `app/rules`, `app/storage` and `app/llm`. See
[architecture and API examples](docs/architecture.md). Existing `python -m
app.ingestion...` command paths are preserved; old free-function imports have
been replaced by class methods.
