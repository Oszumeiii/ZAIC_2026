"""Xuất dữ liệu SFT bằng rejection sampling: giữ lời giải của câu TRAIN đã trả ĐÚNG."""
from __future__ import annotations

import json

import pandas as pd

from app.agent.context import ContextBuilder
from app.agent.prompts import SYSTEM
from app.ingestion.dataset import Split
from app.tools.results import canon


def correct_solutions(ev: pd.DataFrame, logs: list[dict]):
    """Sinh (dòng đánh giá, code) cho mỗi câu đã trả ĐÚNG bằng LLM; code lấy từ mẫu cho đúng kết quả đã được chọn."""
    by_id = {lg["id"]: lg for lg in logs}
    for r in ev[ev.status == "correct"].itertuples():
        lg = by_id[r.id]
        if lg["source"] != "llm":
            continue
        want = canon(r.type, r.answer)
        alive = [x for x in lg["sample_results"] if x is not None]     # `codes` chỉ chứa mẫu không-None
        codes = list(dict.fromkeys(c for c, x in zip(lg["codes"], alive) if x == want))
        if codes:
            yield r, codes[0]


def export_sft(ev: pd.DataFrame, logs: list[dict], split: Split, builder: ContextBuilder, path: str) -> int:
    """Mỗi dòng: {"messages": [system, user(ngữ cảnh), assistant(code)]}. Chỉ dùng tập train, KHÔNG dùng test."""
    if split.answers is None:
        raise ValueError("chỉ được dựng dữ liệu SFT từ tập có đáp án chuẩn (train)")
    qrow = split.questions.set_index("id")
    n_rows = 0
    with open(path, "w", encoding="utf-8") as f:
        for r, code in correct_solutions(ev, logs):
            q = qrow.loc[r.id]
            ctx, _ = builder.build(q.question, q.as_of_date, split.business(q.business_id), split, q.business_id)
            f.write(json.dumps({"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": ctx},
                {"role": "assistant", "content": "```python\n" + code.strip() + "\n```"}]},
                ensure_ascii=False) + "\n")        # 1 lời giải/câu: tránh lệch phân phối về câu dễ
            n_rows += 1
    return n_rows
