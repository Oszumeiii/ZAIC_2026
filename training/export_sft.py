"""Xuất dữ liệu SFT bằng rejection sampling: giữ lời giải của câu TRAIN đã cho đúng đáp án chuẩn."""
from __future__ import annotations

import json

import pandas as pd

from app.agent.context import ContextBuilder
from app.agent.prompts import build_system
from app.ingestion.dataset import Split
from app.tools.results import canon
from evaluation.scoring import match

# Mẫu sinh sau một lần lỗi thường chứa bình luận về lỗi cũ mà prompt SFT không có: dạy model học bình luận vô nghĩa
BAD_MARKERS = ("KeyError", "Sửa lỗi", "Lần trước", "Traceback", "lỗi:")
MAX_COMMENT_LINES = 6
MAX_CODE_CHARS = 3000


def is_clean_code(code: str) -> bool:
    """Code đủ sạch để học: không bình luận về lỗi, không quá nhiều bình luận, không quá dài."""
    if any(marker in code for marker in BAD_MARKERS) or len(code) > MAX_CODE_CHARS:
        return False
    return sum(1 for line in code.splitlines() if line.strip().startswith("#")) <= MAX_COMMENT_LINES


def correct_solutions(ev: pd.DataFrame, logs: list[dict]):
    """Sinh (dòng đánh giá, code) cho mỗi câu đã trả ĐÚNG bằng LLM; code lấy từ mẫu cho đúng kết quả đã được chọn."""
    by_id = {lg["id"]: lg for lg in logs}
    for r in ev[ev.status == "correct"].itertuples():
        lg = by_id[r.id]
        if lg["source"] != "llm":
            continue
        want = canon(r.type, r.answer)
        alive = [x for x in lg["sample_results"] if x is not None]     # `codes` chỉ chứa mẫu không-None
        codes = [c for c in dict.fromkeys(c for c, x in zip(lg["codes"], alive) if x == want) if is_clean_code(c)]
        if codes:
            yield r, codes[0]


def correct_samples(ev: pd.DataFrame, logs: list[dict], per_question: int = 2, first_attempt_only: bool = True):
    """Sinh (dòng đánh giá, code) cho MỌI mẫu cho đúng đáp án chuẩn (không cần đồng thuận), tối đa per_question mẫu
    mỗi câu. Cần log do chạy với collect_all (mỗi mẫu có `val`). Chỉ lấy mẫu đúng ngay lần sinh đầu (không qua tự sửa)."""
    by_id = {lg["id"]: lg for lg in logs}
    for r in ev.itertuples():
        picked: list[str] = []
        alias = r.alias if isinstance(r.alias, list) else None
        for s in by_id[r.id].get("samples") or []:
            if not s or (first_attempt_only and s.get("attempt", 0) != 0) or not is_clean_code(s["code"]):
                continue
            if s["code"] in picked or not match(s["val"], r.gold, alias, s["type"], r.gold_type):
                continue
            picked.append(s["code"])
            if len(picked) >= per_question:
                break
        for code in picked:
            yield r, code


def export_sft(ev: pd.DataFrame, logs: list[dict], split: Split, builder: ContextBuilder, path: str,
               all_samples: bool = False, per_question: int = 2) -> int:
    """Mỗi dòng: {"messages": [system, user(ngữ cảnh), assistant(code)]}. Chỉ dùng tập train, KHÔNG dùng test.
    all_samples=True: lấy mọi mẫu đúng (cần chạy collect_all); mặc định chỉ lời giải của quyết định đồng thuận."""
    if split.answers is None:
        raise ValueError("chỉ được dựng dữ liệu SFT từ tập có đáp án chuẩn (train)")
    qrow = split.questions.set_index("id")
    system = build_system(builder.cfg)
    source = correct_samples(ev, logs, per_question) if all_samples else correct_solutions(ev, logs)
    n_rows = 0
    with open(path, "w", encoding="utf-8") as f:
        for r, code in source:
            q = qrow.loc[r.id]
            ctx, _ = builder.build(q.question, q.as_of_date, split.business(q.business_id), split, q.business_id)
            f.write(json.dumps({"messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": ctx},
                {"role": "assistant", "content": "```python\n" + code.strip() + "\n```"}]},
                ensure_ascii=False) + "\n")
            n_rows += 1
    return n_rows
