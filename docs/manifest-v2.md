# Manifest v2 implementation notes

The physical source is authoritative for identifiers; README is authoritative for
source-specific meanings; Rulebook defines default calculations, with question
instructions and industry exceptions taking precedence. A valid JSON shape or a
matching field name does not prove business meaning.

Pipeline:

1. `SourceInventory.inspect()` enumerates all supported objects, profiles
   counts/samples and fingerprints source files without using a manifest.
2. `BusinessManifestService.build_input()` combines that inventory with README and the entire
   Rulebook. Every object receives a stable `profile:<file>::<object>` evidence ID.
3. Gemini produces v2 or `upgrade_manifests` expands existing v1 mappings offline.
4. `ManifestValidator.validate()` checks exact object/column identity, duplicate
   bindings, relationship endpoints, evidence IDs and structured semantic
   contradictions. It computes unresolved columns independently of the model.
5. Ingestion validates bindings again against objects actually loaded and maps
   source names to physical DuckDB names. Relationships are checked on full data.
6. Catalog exposes trusted semantics, relationship validation counts, coverage,
   capability gaps and retained rules/facts.

V1 remains readable, but invalid references that were silently ignored before
(such as a table name in `columns`) now fail ingestion. Upgrade to v2 first.
Legacy ambiguous groups are not expanded into invented definitions. Unmapped
fields remain null. Evidence inherited during offline migration is retained;
its business interpretation is not independently certified by the migration.

For `biz_123`, the reviewed overlay corrects the conversion column to
`don_vi_sp.HeSoQuyDoi`, separates orders/lines/payments/stock movements, preserves
prescription semantics and adds the documented order-line relation using
`IDCuaHang, MaHoaDon`. Profiling and full-data relationship checks establish its
physical validity; the README provides the business relationship.

Limits intentionally exposed rather than hidden:

- CSV/Excel header detection is bounded and heuristic; multi-row/merged headers
  and repeated totals are not silently cleaned or certified.
- Raw data is retained. Forward filling order groups, deduplication and date
  reconstruction require Rulebook-aware query/normalization logic; ingestion
  does not silently change source rows.
- Profile samples and distinct counts above the cap cannot certify keys.
- A verified join does not permit summing order totals after expanding order
  lines, or joining two many-side fact tables without pre-aggregation.
- Coverage is structural/documentation coverage, not measured answer accuracy.
- `partial` readiness is not executable SQL certification. The SQL-agent pipeline
  still needs query-specific status decoding, return exclusion, temporal and
  grain checks before answering questions.
