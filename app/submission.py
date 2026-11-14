"""Tạo, ghi và kiểm định file nộp submission.jsonl."""
from __future__ import annotations

import json
import os
import tempfile

from app.tools.results import VALID_TYPES

MAX_BYTES = 5 * 1024 * 1024


def make_sub(qid, typ, ans) -> dict:
    if typ in (None, "abstain"):
        return {"id": qid, "type": "abstain"}
    if typ == "unanswerable":
        return {"id": qid, "type": typ, "reason": str(ans)[:300]}
    return {"id": qid, "type": typ, "answer": ans}


def dump_line(sub: dict) -> str:
    """Một dòng JSON chuẩn (cấm NaN/Infinity). Không ghi được thì chuyển câu đó thành abstain thay vì hỏng cả file."""
    try:
        return json.dumps(sub, ensure_ascii=False, default=str, allow_nan=False)
    except ValueError:
        return json.dumps({"id": sub["id"], "type": "abstain"}, ensure_ascii=False)


def fill_missing(subs: list[dict], ids) -> list[dict]:
    """Mỗi id trong questions.jsonl có đúng một dòng, theo thứ tự file câu hỏi; câu chưa chạy = abstain."""
    by_id = {s["id"]: s for s in subs}
    return [by_id.get(i) or {"id": i, "type": "abstain"} for i in ids]


def write_submission(subs: list[dict], ids, path: str) -> list[dict]:
    rows = fill_missing(subs, ids)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".submission.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            for s in rows:
                f.write(dump_line(s) + "\n")
        os.replace(temporary_path, path)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
    return rows


def _bad_const(c):
    raise ValueError(f"hằng không hợp lệ trong JSON: {c}")


def validate_submission(path: str, ids) -> list[str]:
    """Kiểm nhanh trước khi nộp: JSON chuẩn, đủ và duy nhất id, type hợp lệ, có answer khi cần, dưới 5MB."""
    problems, seen = [], set()
    if os.path.getsize(path) > MAX_BYTES:
        problems.append("file > 5MB")
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        try:
            d = json.loads(line, parse_constant=_bad_const)
        except ValueError as e:
            problems.append(f"dòng {n}: {e}")
            continue
        try:
            validate_answer(d)
        except (ValueError, TypeError) as error:
            problems.append(f"dòng {n}: {error}")
            if not isinstance(d, dict) or not isinstance(d.get("id"), str):
                continue
        if d.get("id") in seen:
            problems.append(f"dòng {n}: id trùng {d.get('id')}")
        seen.add(d.get("id"))
        t = d.get("type")
        if t not in VALID_TYPES | {"abstain"}:
            problems.append(f"dòng {n}: type lạ {t!r}")
        elif t not in ("abstain", "unanswerable") and d.get("answer") is None:
            problems.append(f"dòng {n}: thiếu answer")
    problems += [f"thiếu id {i}" for i in list(set(ids) - seen)[:5]]
    problems += [f"id lạ {i}" for i in list(seen - set(ids))[:5]]
    return problems


def validate_answer(row):
    """Validate answer shapes shared with train answers.jsonl; never consult gold values."""
    import math
    from datetime import date
    if not isinstance(row, dict) or not isinstance(row.get('id'), str):
        raise ValueError('Answer must be an object with string id')
    typ, answer = row.get('type'), row.get('answer')
    if typ == 'abstain':
        return
    if typ == 'unanswerable':
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError('unanswerable requires a reason')
        return
    number = lambda v: type(v) in (int, float) and math.isfinite(v)
    valid = False
    if typ in ('number', 'percent'):
        valid = number(answer)
    elif typ == 'boolean':
        valid = type(answer) is bool
    elif typ == 'string':
        valid = isinstance(answer, str)
    elif typ == 'date':
        if isinstance(answer, str):
            candidate = answer + '-01' if len(answer) == 7 else answer
            valid = date.fromisoformat(candidate).isoformat() == candidate
    elif typ == 'list':
        valid = isinstance(answer, list) and all(type(v) in (str, bool) or number(v) for v in answer)
    elif typ == 'table' and isinstance(answer, dict):
        columns, rows = answer.get('columns'), answer.get('rows')
        valid = (isinstance(columns, list) and all(isinstance(c, str) for c in columns)
                 and isinstance(rows, list) and all(isinstance(r, list) and len(r) == len(columns)
                    and all(v is None or type(v) in (str, bool) or number(v) for v in r) for r in rows))
    if not valid:
        raise ValueError(f'Invalid answer format for {typ}')
