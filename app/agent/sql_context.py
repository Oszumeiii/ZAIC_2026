"""Build SQL context from adapter-produced DuckDB and catalog, never answer keys."""
import hashlib
import json
import re
from pathlib import Path

from app.ingestion.processing.pipeline import IngestionPipeline
from app.ingestion.sources.adapters.common import TabularLayout
from app.tools.sql import SQLExecutor
from app.tools.timeutil import describe_period


class SQLContextBuilder:
    def __init__(self, cfg):
        self.cfg = cfg
        self.prepared = {}

    def prepare(self, split, business_id):
        root = Path(split.root).resolve()
        key = (str(root), business_id)
        if key in self.prepared:
            return self.prepared[key]
        business_dir = root / "data" / business_id
        if business_dir.resolve().parent != root / "data":
            raise ValueError("business_id không hợp lệ")
        output = Path(self.cfg.database_dir) / business_id / "current"
        database = output / "business.duckdb"
        catalog_path = output / "catalog.json"
        if self.cfg.rebuild_database or not database.exists() or not catalog_path.exists():
            manifest = root / "manifests" / f"{business_id}.json"
            catalog = IngestionPipeline.ingest(business_dir, output, manifest if manifest.exists() else None)
        else:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if catalog["business_id"] != business_id:
            raise ValueError("Catalog không thuộc business đang hỏi")
        tables = []
        with SQLExecutor.connect(database, split.calendar) as connection:
            for table in catalog["tables"]:
                name = TabularLayout.quote_identifier(table["physical_name"])
                _, rows = SQLExecutor.query(connection, f"SELECT * FROM {name} LIMIT 3")
                columns = []
                for column in table["columns"]:
                    item = {k: column.get(k) for k in (
                        "physical_name", "source_name", "duckdb_type", "description",
                        "semantic_name", "semantic_status", "unit_basis", "aggregation", "time_role")}
                    col = TabularLayout.quote_identifier(column["physical_name"])
                    if (column.get("time_role") or re.search(r"ngay|date|thoi_gian", column["physical_name"])):
                        year = re.search(r"\b(?:19|20)\d{2}\b", " ".join(table["source_objects"]))
                        expression = (f"vn_date_year(CAST({col} AS VARCHAR), {year[0]})" if year
                                      else f"vn_date(CAST({col} AS VARCHAR))")
                        _, span = SQLExecutor.query(connection, f"SELECT min({expression}), max({expression}) FROM {name}")
                        item["date_range"] = span[0]
                    if re.search(r"trang_thai|status|ket_qua|tinh_trang", column["physical_name"]):
                        _, counts = SQLExecutor.query(connection,
                            f"SELECT {col}, count(*) AS n FROM {name} GROUP BY 1 ORDER BY 2 DESC LIMIT 15")
                        item["values"] = counts
                    columns.append(item)
                tables.append({"name": table["physical_name"], "sources": table["source_files"],
                               "source_objects": table["source_objects"], "role": table.get("table_role"),
                               "grain": table.get("grain"), "row_count": table["row_count"],
                               "columns": columns, "sample_rows": rows})
        readme_path = business_dir / "README.md"
        context = {"tables": tables, "relationships": catalog.get("relationships", []),
                   "facts": catalog.get("facts", []), "unresolved": catalog.get("unresolved", []),
                   "rulebook": split.rulebook,
                   "readme": readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""}
        if split.calendar is not None:
            context["calendar"] = {"name": "calendar", "columns": list(split.calendar.columns),
                                   "source": "calendar.csv",
                                   "sha256": hashlib.sha256(split.calendar.to_json(
                                       orient="split", date_format="iso").encode()).hexdigest()}
        with database.open("rb") as stream:
            fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
        self.prepared[key] = database, context, fingerprint
        return self.prepared[key]

    @staticmethod
    def question_context(context, original, rewritten, as_of, calendar):
        return {**context, "original_question": original, "question": rewritten,
                "as_of_date": str(as_of), "time_hint": describe_period(rewritten, as_of, calendar)}
