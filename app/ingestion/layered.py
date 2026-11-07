"""Business ingestion API: raw rows, typed frames and reviewable semantic metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.text import colnorm
from .readers import READERS, RawTable, issue, read_csv
from .typing import convert_table


@dataclass
class IngestedTable:
    raw: RawTable
    typed: pd.DataFrame
    columns: list[dict]
    flags: list[list[str]]
    issues: list[dict]


@dataclass
class IngestedBusiness:
    business_id: str
    readme: str
    rulebook: str
    tables: dict[str, IngestedTable] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    semantic: dict = field(default_factory=dict)

    @property
    def frames(self):
        """Typed frames with original names. Provenance is separate from data columns."""
        return {name: table.typed for name, table in self.tables.items()}


def ingest_calendar(path, *, rulebook=""):
    """Shared challenge calendar, with an explicit schema for its known columns."""
    raw = read_csv(Path(path), {})[0]
    columns = {name: {"type": kind} for name, kind in {
        "date": "date", "weekday": "integer", "lunar_day": "integer", "lunar_month": "integer",
        "lunar_year": "integer", "lunar_leap": "boolean", "holiday": "string",
    }.items() if name in raw.headers}
    frame, metadata, flags, issues = convert_table(raw, "", {"columns": columns})
    result = IngestedBusiness("_shared", "Lịch dùng chung của split", rulebook)
    result.tables[raw.name] = IngestedTable(raw, frame, metadata, flags, issues)
    result.semantic = {"views": {}, "policy": "Shared calendar with explicit column types"}
    return result


def ingest_business(directory, *, rulebook="", overrides=None, strict=True):
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    overrides = overrides or {}
    readme_path = directory / "README.md"
    readme = readme_path.read_text(encoding="utf-8-sig") if readme_path.exists() else ""
    result = IngestedBusiness(directory.name, readme, rulebook)
    unknown_files = set(overrides.get("files", {})) - {p.name for p in directory.iterdir() if p.is_file()}
    if unknown_files:
        raise ValueError(f"Unknown override files: {sorted(unknown_files)}")
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() in (".md", ".txt"):
            continue
        reader = READERS.get(path.suffix.lower())
        if reader is None:
            problem = issue("unsupported_file", file=str(path), review_required=True)
            result.issues.append(problem)
            if strict:
                raise ValueError(f"Unsupported input: {path}")
            continue
        try:
            file_options = overrides.get("files", {}).get(path.name, {})
            for raw in reader(path, file_options):
                if raw.name in result.tables:
                    raise ValueError(f"Logical table collision: {raw.name}; rename source files")
                options = overrides.get("tables", {}).get(raw.name, {})
                frame, columns, flags, issues = convert_table(raw, readme, options)
                frame.attrs["source"] = raw.source
                frame.attrs["positions"] = raw.positions
                result.tables[raw.name] = IngestedTable(raw, frame, columns, flags, issues)
        except Exception as exc:
            if strict:
                raise RuntimeError(f"Ingestion failed for {path}: {exc}") from exc
            result.issues.append(issue("file_read_failed", file=str(path), error=str(exc), review_required=True))
    unknown_tables = set(overrides.get("tables", {})) - set(result.tables)
    if unknown_tables:
        raise ValueError(f"Unknown override tables: {sorted(unknown_tables)}")
    return finish_semantic(result, overrides)


def finish_semantic(result, overrides):
    keys = []
    aliases = {}
    for name, table in result.tables.items():
        for column in table.columns:
            aliases.setdefault(column["search_alias"], []).append({"table": name, "column": column["name"]})
            if column["identifier"]:
                series = table.typed[column["name"]].dropna()
                keys.append({"table": name, "column": column["name"], "alias": colnorm(column["name"]),
                             "non_null": len(series), "unique": int(series.nunique()),
                             "unique_non_null": bool(len(series) and series.is_unique)})
    # Names alone are insufficient evidence to establish a foreign key.
    result.semantic = {"aliases": aliases, "key_candidates": keys,
                       "confirmed_links": overrides.get("links", []),
                       "views": overrides.get("views", {}),
                       "policy": "Identity views by default; no inferred revenue, joins or unit conversions."}
    return result
