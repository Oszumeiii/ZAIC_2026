"""Dựng kho ví dụ few-shot từ lời giải đã đúng trên tập train (chỉ dùng train, không dùng test)."""
from __future__ import annotations

import pandas as pd

from app.agent.exemplars import ExemplarBank
from app.ingestion.dataset import Split
from training.export_sft import correct_solutions


def build_exemplar_bank(ev: pd.DataFrame, logs: list[dict], split: Split, path: str) -> int:
    """Mỗi dòng: {"id", "business_id", "question", "type", "code"}. Trả số ví dụ đã ghi."""
    if split.answers is None:
        raise ValueError("chỉ được dựng kho ví dụ từ tập có đáp án chuẩn (train)")
    qrow = split.questions.set_index("id")
    items = [{"id": r.id, "business_id": qrow.loc[r.id].business_id, "question": qrow.loc[r.id].question,
              "type": r.type, "code": code.strip()} for r, code in correct_solutions(ev, logs)]
    ExemplarBank.dump(items, path)
    return len(items)
