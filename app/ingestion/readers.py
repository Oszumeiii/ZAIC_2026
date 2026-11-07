"""Loss-aware readers. Raw rows keep source positions; no business cleaning."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.text import norm


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass
class RawTable:
    name: str
    source: dict
    headers: list[str]
    rows: list[list]
    positions: list[dict]
    issues: list[dict] = field(default_factory=list)
    cell_metadata: dict = field(default_factory=dict)


def issue(code, **details):
    return {"code": code, **details}


def source_info(path: Path) -> dict:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"file": str(path.resolve()), "sha256": h.hexdigest(), "bytes": path.stat().st_size}


def choose_header(rows, override=None):
    if override is not None:
        if not isinstance(override, int) or not 1 <= override <= len(rows):
            raise ValueError("header_row must be a valid 1-based record/worksheet row")
        return override - 1, "override"
    candidates = []
    for i, row in enumerate(rows[:30]):
        present = [x for x in row if x is not None and str(x).strip()]
        if not present:
            continue
        strings = [x for x in present if isinstance(x, str)]
        if len(strings) < max(1, len(present) * .6):
            continue
        known = sum(bool(re.search(r"(^|[ _])(ma|id|ngay|ten|gia|tien|luong|date|name|amount|qty|code|stt|tong|khach)([ _]|$)", norm(x))) for x in strings)
        score = len(present) + known * 3 - i * .05
        if len(present) == 1 and any(len(r) > 1 and sum(x is not None for x in r) > 1 for r in rows[i+1:i+4]):
            score -= 100
        candidates.append((score, i))
    if not candidates:
        raise ValueError("Cannot identify header; set header_row explicitly")
    return max(candidates)[1], "heuristic"


def assemble(path, name, source, rows, positions, options, cells=None):
    header, method = choose_header(rows, options.get("header_row"))
    width = max(len(r) for r in rows)
    raw_names = list(rows[header]) + [None] * (width - len(rows[header]))
    names, seen = [], set()
    issues = [issue("header_detected", row=positions[header], method=method)]
    for i, value in enumerate(raw_names):
        original = "" if value is None else str(value)
        base = original if original.strip() else f"__unnamed_{i+1}"
        name_candidate, n = base, 2
        while name_candidate.casefold() in seen:
            name_candidate = f"{base}__{n}"
            n += 1
        if name_candidate != original:
            issues.append(issue("header_renamed", column_index=i, original=value, name=name_candidate))
        seen.add(name_candidate.casefold())
        names.append(name_candidate)
    source.update(header=positions[header], header_method=method, original_headers=raw_names,
                  preamble=rows[:header])
    data, data_pos = [], []
    for values, pos in zip(rows[header+1:], positions[header+1:]):
        if len(values) != width:
            issues.append(issue("ragged_row", position=pos, expected=width, actual=len(values)))
        data.append(values + [None] * (width - len(values)))
        data_pos.append(pos)
    return RawTable(name, source, names, data, data_pos, issues, cells or {})


def read_csv(path: Path, options: dict) -> list[RawTable]:
    content = path.read_bytes()
    encoding = options.get("encoding")
    fallback = False
    if not encoding:
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        else:
            for candidate in ("utf-8-sig", "cp1258", "cp1252"):
                try:
                    content.decode(candidate, errors="strict")
                    encoding = candidate
                    break
                except UnicodeDecodeError:
                    continue
            fallback = encoding not in ("utf-8-sig", "utf-16")
    if not encoding:
        raise ValueError("Unknown encoding; set encoding explicitly")
    text = content.decode(encoding, errors="strict")
    delimiter = options.get("delimiter")
    delimiter_fallback = False
    if not delimiter:
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter_fallback = True
            delimiter = max(",;\t|", key=lambda d: sum(line.count(d) for line in text.splitlines()[:30]))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    rows, positions, previous = [], [], 0
    for row in reader:
        positions.append({"record": len(rows)+1, "line_start": previous+1, "line_end": reader.line_num})
        rows.append(row)
        previous = reader.line_num
    if not rows:
        raise ValueError("Empty CSV")
    source = source_info(path) | {"format": "csv", "encoding": encoding, "delimiter": delimiter}
    table = assemble(path, path.stem, source, rows, positions, options)
    if fallback:
        table.issues.append(issue("encoding_fallback", encoding=encoding, review_required=True))
    if delimiter_fallback:
        table.issues.append(issue("delimiter_fallback", delimiter=delimiter, review_required=True))
    return [table]


def read_excel(path: Path, options: dict) -> list[RawTable]:
    from openpyxl import load_workbook
    formula_book = load_workbook(path, read_only=True, data_only=False)
    try:
        value_book = load_workbook(path, read_only=True, data_only=True)
        try:
            source = source_info(path) | {"format": "xlsx"}
            tables = []
            for sheet in formula_book:
                sheet_options = options | options.get("sheets", {}).get(sheet.title, {})
                rows, positions, cells, issues = [], [], {}, []
                for i, (raw_row, cached_row) in enumerate(zip(sheet.iter_rows(), value_book[sheet.title].iter_rows()), 1):
                    row = []
                    for j, (cell, cached) in enumerate(zip(raw_row, cached_row), 1):
                        value = cell.value
                        if cell.data_type == "f":
                            cells[f"{i}:{j}"] = {"formula": value, "cached": cached.value,
                                                    "number_format": cell.number_format}
                            value = cached.value
                            if value is None:
                                issues.append(issue("formula_cache_missing", row=i, column=j, formula=cell.value))
                        elif cell.data_type == "e":
                            issues.append(issue("excel_error", row=i, column=j, value=value))
                            cells[f"{i}:{j}"] = {"error": value}
                            value = None
                        elif value is not None and cell.number_format not in ("General", "@"):
                            cells[f"{i}:{j}"] = {"number_format": cell.number_format}
                        row.append(value)
                    rows.append(row)
                    positions.append({"row": i})
                if not rows or not any(any(x is not None for x in row) for row in rows):
                    tables.append(RawTable(f"{path.stem}::{sheet.title}", source | {"sheet": sheet.title},
                                           [], [], [], [issue("empty_sheet")]))
                    continue
                table = assemble(path, f"{path.stem}::{sheet.title}", source | {"sheet": sheet.title},
                                 rows, positions, sheet_options, cells)
                table.issues.extend(issues)
                tables.append(table)
            return tables
        finally:
            value_book.close()
    finally:
        formula_book.close()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_sqlite(path: Path, options: dict) -> list[RawTable]:
    tables = []
    source = source_info(path) | {"format": "sqlite"}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as con:
        names = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        for (name,) in names:
            columns = con.execute(f"PRAGMA table_info({quote_identifier(name)})").fetchall()
            headers = [c[1] for c in columns]
            pk = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
            # Primary keys give stable locators even for WITHOUT ROWID tables.
            query = f"SELECT * FROM {quote_identifier(name)}"
            if pk:
                query += " ORDER BY " + ",".join(map(quote_identifier, pk))
            rows = [list(row) for row in con.execute(query)]
            positions = [{"record": i+1, "primary_key": {c: row[headers.index(c)] for c in pk}}
                         for i, row in enumerate(rows)]
            warnings = [] if pk else [issue("sqlite_no_primary_key", review_required=True)]
            tables.append(RawTable(f"{path.stem}::{name}", source | {"table": name, "primary_key": pk},
                                   headers, rows, positions, warnings))
    return tables


READERS = {".csv": read_csv, ".xlsx": read_excel, ".xlsm": read_excel,
           ".sqlite": read_sqlite, ".db": read_sqlite, ".sqlite3": read_sqlite}
