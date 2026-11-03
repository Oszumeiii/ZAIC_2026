"""Nạp một split (questions/answers/rulebook/calendar) và dữ liệu từng business."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field

import pandas as pd


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def find_root_dir(split_dir: str) -> str:
    """Tìm thư mục chứa questions.jsonl (phòng khi cấu trúc lồng thêm một cấp)."""
    for dirpath, _dirs, files in os.walk(split_dir):
        if "questions.jsonl" in files:
            return dirpath
    raise FileNotFoundError(split_dir)


def fix_header(df: pd.DataFrame, min_fill: float = 0.6) -> pd.DataFrame:
    """Sheet kiểu sổ tay: tiêu đề nằm ở dòng 1-3 chứ không phải dòng 0 (cột thành "Unnamed: n") -> dời tiêu đề lên."""
    cols = [str(c) for c in df.columns]
    if len(cols) < 2 or sum(c.startswith("Unnamed") for c in cols) / len(cols) < 0.5:
        return df
    for i in range(min(10, len(df))):
        row = df.iloc[i]
        if row.notna().sum() >= min_fill * len(cols) and all(isinstance(v, str) for v in row.dropna()):
            new = df.iloc[i + 1:].copy()
            names, seen = [], {}
            for j, v in enumerate(row):
                name = str(v).strip() if pd.notna(v) else f"col_{j}"
                seen[name] = seen.get(name, 0) + 1
                names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
            new.columns = names
            return new.reset_index(drop=True).infer_objects()
    return df


def load_tables(biz_dir: str) -> dict[str, pd.DataFrame]:
    """Đọc mọi csv/xlsx/sqlite trong thư mục business thành dict tên -> DataFrame."""
    out: dict[str, pd.DataFrame] = {}
    for fname in sorted(os.listdir(biz_dir)):
        path, (stem, ext) = os.path.join(biz_dir, fname), os.path.splitext(fname)
        ext = ext.lower()
        try:
            if ext == ".csv":
                out[stem] = fix_header(pd.read_csv(path))
            elif ext in (".xlsx", ".xls"):
                for sheet, df in pd.read_excel(path, sheet_name=None).items():
                    out[f"{stem}::{sheet}"] = fix_header(df)
            elif ext in (".sqlite", ".db", ".sqlite3"):
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                for table in pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", con)["name"]:
                    out[f"{stem}::{table}"] = pd.read_sql(f'SELECT * FROM "{table}"', con)
                con.close()
        except Exception as e:
            print("Lỗi đọc", path, e)
    return out


@dataclass
class Business:
    tables: dict
    readme: str
    cache: dict = field(default_factory=dict)   # kết quả dựng sẵn dùng lại giữa các câu (bảng đã gộp, profile...)


@dataclass
class Split:
    root: str
    questions: pd.DataFrame
    answers: pd.DataFrame | None
    rulebook: str
    calendar: pd.DataFrame | None
    _businesses: dict = field(default_factory=dict)

    def business(self, biz_id) -> Business:
        if biz_id not in self._businesses:
            directory = f"{self.root}/data/{biz_id}"
            readme_path = f"{directory}/README.md"
            readme = open(readme_path, encoding="utf-8").read() if os.path.exists(readme_path) else ""
            self._businesses[biz_id] = Business(tables=load_tables(directory), readme=readme)
        return self._businesses[biz_id]


def load_split(split_dir: str) -> Split:
    root = find_root_dir(split_dir)
    questions = pd.DataFrame(read_jsonl(f"{root}/questions.jsonl"))
    answers_path = f"{root}/answers.jsonl"
    answers = pd.DataFrame(read_jsonl(answers_path)) if os.path.exists(answers_path) else None
    rulebook_path, calendar_path = f"{root}/rulebook.md", f"{root}/calendar.csv"
    rulebook = open(rulebook_path, encoding="utf-8").read() if os.path.exists(rulebook_path) else ""
    calendar = pd.read_csv(calendar_path) if os.path.exists(calendar_path) else None
    return Split(root=root, questions=questions, answers=answers, rulebook=rulebook, calendar=calendar)
