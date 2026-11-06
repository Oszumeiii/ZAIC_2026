from itertools import islice
from pathlib import Path
from typing import Any, Iterator, Sequence
import argparse
import json
import os
import tempfile

from jsonschema import Draft202012Validator
import duckdb

from app.catalog.builder import CatalogBuilder
from app.catalog.relationships import RelationshipService
from app.ingestion.sources.adapters.common import SourceTable, TabularLayout
from app.ingestion.processing.normalization import IdentifierNormalizer, ValueNormalizer
from app.ingestion.sources.inventory import SourceInventory
from app.manifests.service import SCHEMA_FILE
from app.manifests.validation import ManifestValidator
from app.rules.rulebook import Rulebook
from app.storage.json_store import JsonStore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_SCHEMA_FILE = SCHEMA_FILE
CATALOG_SCHEMA_FILE = PROJECT_ROOT / "schemas" / "catalog.schema.json"
BATCH_SIZE = 1000
PROFILE_SAMPLE_SIZE = 1000
DUCKDB_TYPES = {"BIGINT", "DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP", "VARCHAR"}


class IngestionPipeline:
    """Load source tables into DuckDB and publish a validated business catalog."""
    # Hàm tĩnh để trích xuất các tiêu đề cột dự kiến từ manifest, dựa trên phiên bản schema và tên file nguồn. Nếu manifest là None, trả về tập rỗng. Nếu schema_version là "2.0", lấy tất cả các source_name của các cột trong các bảng có source_file khớp. Nếu schema_version khác, so sánh tên file nguồn với các source_files trong manifest và lấy tên cột cuối cùng sau dấu chấm.
    @staticmethod
    def _expected_headers(
        manifest: dict[str, Any] | None,
        source_file: str,
    ) -> set[str]:
        if manifest is None:
            return set()
        if manifest.get("schema_version") == "2.0":
            return {c["source_name"] for t in manifest["tables"] if t["source_file"] == source_file
                    for c in t["columns"]}
        from pathlib import PurePosixPath

        source_basename = PurePosixPath(source_file).name
        return {
            column
            for table in manifest.get("tables", [])
            if any(
                PurePosixPath(candidate).name == source_basename
                for candidate in table.get("source_files", [])
            )
            for source_column in table.get("columns", {})
            for column in [source_column.rsplit(".", 1)[-1]]
        }


    # Hàm này đọc các bảng nguồn từ một file nguồn cụ thể, dựa trên đường dẫn đến file, thư mục của doanh nghiệp và manifest. Nó xác định các tiêu đề cột dự kiến từ manifest và sử dụng SourceInventory để đọc dữ liệu từ file nguồn, trả về một iterator các đối tượng SourceTable.
    @staticmethod
    def _read_source(
        path: Path,
        business_dir: Path,
        manifest: dict[str, Any] | None,
    ) -> Iterator[SourceTable]:
        relative_path = path.relative_to(business_dir).as_posix()
        expected = IngestionPipeline._expected_headers(manifest, relative_path)
        yield from SourceInventory.read(path, business_dir, expected)

    @staticmethod
    def _semantic_description(
        manifest: dict[str, Any] | None,
        source_file: str,
        source_object: str,
        headers: tuple[str | None, ...],
    ) -> dict[str, str]:
        table, column_mappings, _ = CatalogBuilder.choose_table(
            manifest, source_file, source_object, headers
        )
        if table is None:
            return {}
        return column_mappings

    @staticmethod
    def _base_table_name(source_file: str, source_object: str) -> str:
        source_stem = Path(source_file).stem
        suffix = "" if source_object == source_stem else f"_{source_object}"
        return IdentifierNormalizer.normalize(f"{source_stem}{suffix}", fallback="source_table")

    @staticmethod
    def _unique_table_name(base: str, used_names: set[str]) -> str:
        candidate = base
        suffix = 2
        while candidate.casefold() in used_names:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_names.add(candidate.casefold())
        return candidate

    @staticmethod
    def _column_type(
        column_index: int,
        sample_rows: list[tuple[Any, ...]],
        source_name: str | None,
        descriptions: dict[str, str],
    ) -> str:
        semantic_description = descriptions.get(source_name or "")
        semantic_name = (
            IdentifierNormalizer.normalize(semantic_description)
            if semantic_description is not None
            else None
        )
        force_text = ValueNormalizer.is_identifier(semantic_name, source_name)
        return ValueNormalizer.infer_type(
            (row[column_index] for row in sample_rows),
            force_text=force_text,
        )

    @staticmethod
    def _write_source_table(
        connection: duckdb.DuckDBPyConnection,
        source: SourceTable,
        manifest: dict[str, Any] | None,
        used_names: set[str],
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        if not source.source_headers:
            raise ValueError(
                f"No columns found in {source.source_file} / {source.source_object}."
            )

        physical_columns = IdentifierNormalizer.unique(source.source_headers)
        base_table_name = IngestionPipeline._base_table_name(source.source_file, source.source_object)
        physical_table_name = IngestionPipeline._unique_table_name(base_table_name, used_names)
        rows = iter(source.rows)
        sample_rows = list(islice(rows, PROFILE_SAMPLE_SIZE))
        descriptions = IngestionPipeline._semantic_description(
            manifest,
            source.source_file,
            source.source_object,
            source.source_headers,
        )
        types = [
            IngestionPipeline._column_type(index, sample_rows, source.source_headers[index], descriptions)
            for index in range(len(physical_columns))
        ]
        unsupported_types = set(types) - DUCKDB_TYPES
        if unsupported_types:
            raise ValueError(f"Unsupported inferred DuckDB types: {sorted(unsupported_types)}")

        column_sql = ", ".join(
            f"{TabularLayout.quote_identifier(name)} {duckdb_type}"
            for name, duckdb_type in zip(physical_columns, types, strict=True)
        )
        connection.execute(
            f"CREATE TABLE {TabularLayout.quote_identifier(physical_table_name)} ({column_sql})"
        )
        placeholders = ", ".join("?" for _ in physical_columns)
        insert_sql = (
            f"INSERT INTO {TabularLayout.quote_identifier(physical_table_name)} "
            f"VALUES ({placeholders})"
        )
        writer = _TableWriter(
            connection,
            insert_sql,
            types,
            source,
            physical_table_name,
            physical_columns,
        )

        try:
            for start in range(0, len(sample_rows), BATCH_SIZE):
                writer.insert_batch(sample_rows[start : start + BATCH_SIZE])
            batch: list[tuple[Any, ...]] = []
            for row in rows:
                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    writer.insert_batch(batch)
                    batch = []
            if batch:
                writer.insert_batch(batch)
        except Exception as error:
            raise RuntimeError(
                f"Failed to load {source.source_file} / {source.source_object} "
                f"into DuckDB table {physical_table_name}."
            ) from error

        profiles = [
            {
                "duckdb_type": writer.types[index],
                "source_type": (
                    source.source_types[index]
                    if index < len(source.source_types)
                    else None
                ),
                "null_count": writer.null_counts[index],
            }
            for index, duckdb_type in enumerate(types)
        ]
        table_catalog, issues = CatalogBuilder.build_table(
            source_file=source.source_file,
            source_object=source.source_object,
            source_headers=source.source_headers,
            physical_table_name=physical_table_name,
            header_row_number=source.header_row_number,
            header_status=source.header_status,
            row_count=writer.row_count,
            column_profiles=profiles,
            manifest=manifest,
        )
        for index, inferred_type in writer.widened_columns.items():
            source_name = source.source_headers[index]
            issues.append(
                {
                    "topic": (
                        f"type:{source.source_file}:{source.source_object}:"
                        f"{source_name or index + 1}"
                    ),
                    "reason": (
                        f"Column {source_name or index + 1!r} contained values "
                        f"incompatible with inferred {inferred_type}; it was stored "
                        "as VARCHAR to preserve those values."
                    ),
                    "needed_evidence": (
                        "Confirm whether the mixed values are intentional or whether "
                        "the source contains summary rows that should be separated."
                    ),
                }
            )
        return table_catalog, issues

    @staticmethod
    def _load_manifest(
        manifest_path: Path | None,
        business_id: str,
    ) -> dict[str, Any] | None:
        if manifest_path is None:
            return None
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Business manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("business_id") != business_id:
            raise ValueError(
                f"Manifest business_id {manifest.get('business_id')!r} does not match "
                f"input directory {business_id!r}."
            )
        schema = json.loads(MANIFEST_SCHEMA_FILE.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(manifest)
        return manifest

    @staticmethod
    def ingest(
        business_dir: Path,
        output_dir: Path,
        manifest_path: Path | None = None,
    ) -> dict[str, Any]:
        business_dir = business_dir.resolve() # Đảm bảo rằng business_dir là một đường dẫn tuyệt đối và chuẩn hóa các ký tự đặc biệt trong đường dẫn.
        output_dir.mkdir(parents=True, exist_ok=True) # Tạo thư mục output_dir nếu nó chưa tồn tại, bao gồm tất cả các thư mục cha cần thiết.
        business_id = business_dir.name # Lấy tên thư mục cuối cùng của business_dir làm business_id.
        manifest = IngestionPipeline._load_manifest(manifest_path, business_id)  # Nếu manifest_path được cung cấp, tải manifest từ file JSON và xác thực nó. Nếu không, manifest sẽ là None.
        source_files = SourceInventory.discover(business_dir)
        if not source_files:
            raise ValueError(f"No supported source files found in {business_dir}.")

        database_path = output_dir / "business.duckdb"
        catalog_path = output_dir / "catalog.json"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".business-",
            suffix=".duckdb",
            dir=output_dir,
        )
        os.close(descriptor)
        temporary_database_path = Path(temporary_name)
        temporary_database_path.unlink()
        connection: duckdb.DuckDBPyConnection | None = None
        tables: list[dict[str, Any]] = []
        relationship_declarations: list[dict[str, Any]] = []
        unresolved: list[dict[str, str]] = []
        used_table_names: set[str] = set()

        try:
            connection = duckdb.connect(str(temporary_database_path))
            connection.execute("BEGIN TRANSACTION")
            for source_path in source_files:
                for source in IngestionPipeline._read_source(source_path, business_dir, manifest):
                    relationship_declarations.extend(source.relationships)
                    table_catalog, issues = IngestionPipeline._write_source_table(
                        connection,
                        source,
                        manifest,
                        used_table_names,
                    )
                    tables.append(table_catalog)
                    unresolved.extend(issues)
            if not tables:
                raise ValueError(f"No readable source tables found in {business_dir}.")
            if manifest is not None:
                observed = {"objects": [{"source_file": t["source_files"][0],
                                          "source_object": t["source_objects"][0],
                                          "header_row_number": t["header"]["source_row_number"],
                                          "header_status": t["header"]["status"],
                                          "columns": t["columns"]} for t in tables]}
                report = ManifestValidator.validate(manifest, observed)
                if report["errors"]:
                    raise ValueError("Invalid manifest source bindings: " + "; ".join(report["errors"]))
                unresolved.extend(report["unresolved"])
            if manifest is None:
                unresolved.append(
                    {
                        "topic": "business_manifest",
                        "reason": "No business manifest was supplied, so semantic "
                        "column mappings are unavailable.",
                        "needed_evidence": "Generate or provide the business manifest "
                        "for this business.",
                    }
                )
            relationships, relationship_issues = RelationshipService.from_declarations(
                relationship_declarations,
                tables,
            )
            unresolved.extend(relationship_issues)
            if manifest and manifest.get("schema_version") == "2.0":
                documented_relationships, documented_issues = RelationshipService.from_manifest(
                    connection, manifest["relationships"], tables)
                relationships.extend(documented_relationships)
                unresolved.extend(documented_issues)
            if len(tables) > 1 and not relationships and not relationship_declarations:
                unresolved.append(
                    {
                        "topic": "relationships",
                        "reason": "No database-declared relationships were found. "
                        "Matching column names alone are not treated as join evidence.",
                        "needed_evidence": "Document and validate join keys before "
                        "using cross-table joins.",
                    }
                )
            connection.execute("COMMIT")
            connection.close()
            connection = None

            catalog = CatalogBuilder.build(
                business_id=business_id,
                database_file=database_path.name,
                tables=tables,
                relationships=relationships,
                unresolved=unresolved,
                manifest=manifest,
            )
            catalog["quality"] = Rulebook.assess_capabilities(catalog)
            catalog_schema = json.loads(CATALOG_SCHEMA_FILE.read_text(encoding="utf-8"))
            Draft202012Validator(catalog_schema).validate(catalog)
            os.replace(temporary_database_path, database_path)
            JsonStore.write(catalog, catalog_path)
            if manifest is not None:
                JsonStore.write(manifest, output_dir / "manifest.json")
            return catalog
        except Exception:
            if connection is not None:
                try:
                    connection.execute("ROLLBACK")
                finally:
                    connection.close()
            temporary_database_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def main(argv: Sequence[str] | None = None) -> int:
        parser = argparse.ArgumentParser(
            description="Ingest structured business files into DuckDB and build a catalog."
        )
        parser.add_argument("--business-dir", type=Path, required=True)
        parser.add_argument("--output-dir", type=Path, required=True)
        parser.add_argument("--manifest", type=Path)
        args = parser.parse_args(argv)

        catalog = IngestionPipeline.ingest(args.business_dir, args.output_dir, args.manifest)
        print(
            f"Ingested {catalog['business_id']}: {len(catalog['tables'])} tables "
            f"into {args.output_dir / 'business.duckdb'}; "
            f"{len(catalog['unresolved'])} unresolved catalog items."
        )
        return 0


# class giúp chuẩn hóa các lô dữ liệu và theo dõi số lượng hàng và giá trị null cho một bảng vật lý. Nó nhận kết nối DuckDB, câu lệnh SQL chèn, kiểu dữ liệu của các cột và thông tin về nguồn dữ liệu. Phương thức insert_batch chuẩn hóa từng hàng trong lô, đếm số giá trị null cho mỗi cột và chèn lô vào bảng DuckDB.
class _TableWriter:
    """Normalize batches and track row/null counts for one physical table."""

    def __init__(
        self, connection, insert_sql, types, source, table_name, column_names
    ):
        self.connection = connection
        self.insert_sql = insert_sql
        self.types = types
        self.source = source
        self.table_name = table_name
        self.column_names = column_names
        self.null_counts = [0] * len(types)
        self.row_count = 0
        self.widened_columns = {}

    def insert_batch(self, batch: list[tuple[Any, ...]]) -> None:
        normalized_batch = []
        for row in batch:
            if len(row) != len(self.types):
                raise ValueError(
                    f"Row in {self.source.source_file} / {self.source.source_object} has "
                    f"{len(row)} values for {len(self.types)} columns."
                )
            normalized_values = []
            for index, (value, duckdb_type) in enumerate(
                zip(row, self.types, strict=True)
            ):
                try:
                    normalized = ValueNormalizer.normalize(value, duckdb_type)
                except ValueError:
                    if duckdb_type == "VARCHAR":
                        raise
                    self._widen_column(index, duckdb_type)
                    normalized = ValueNormalizer.normalize(value, "VARCHAR")
                normalized_values.append(normalized)
            normalized = tuple(normalized_values)
            normalized_batch.append(normalized)
        if normalized_batch:
            normalized_batch = [
                tuple(
                    ValueNormalizer.normalize(value, duckdb_type)
                    for value, duckdb_type in zip(row, self.types, strict=True)
                )
                for row in normalized_batch
            ]
            self.connection.executemany(self.insert_sql, normalized_batch)
            self.row_count += len(normalized_batch)
            for row in normalized_batch:
                for index, value in enumerate(row):
                    if value is None:
                        self.null_counts[index] += 1

    def _widen_column(self, index: int, inferred_type: str) -> None:
        self.connection.execute(
            f"ALTER TABLE {TabularLayout.quote_identifier(self.table_name)} "
            f"ALTER COLUMN {TabularLayout.quote_identifier(self.column_names[index])} "
            "SET DATA TYPE VARCHAR"
        )
        self.types[index] = "VARCHAR"
        self.widened_columns.setdefault(index, inferred_type)


if __name__ == "__main__":
    raise SystemExit(IngestionPipeline.main())
