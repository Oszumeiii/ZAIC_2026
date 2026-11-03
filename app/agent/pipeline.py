"""Suy luận trên một split không cần đáp án: ghi submission.jsonl đủ mọi id, có kiểm định."""
from __future__ import annotations

import json
import os

import pandas as pd

from app import submission
from app.agent.solver import Solver
from app.config import Config
from app.ingestion.dataset import load_split
from app.llm.base import LLMBackend


def run_inference(cfg: Config, backend: LLMBackend | None, use_llm: bool = True):
    """Dự đoán split ở cfg.test_dir, ghi submission.jsonl + log. Trả (subs, logs, problems)."""
    split = load_split(cfg.test_dir)
    solver = Solver(cfg, backend)
    subs, logs = solver.solve_split(split, "test", limit_q=cfg.limit_test, use_llm=use_llm,
                                    time_budget=cfg.time_budget_s)
    ids = list(split.questions["id"])
    path = f"{cfg.out_dir}/submission.jsonl"
    rows = submission.write_submission(subs, ids, path)
    os.makedirs(f"{cfg.out_dir}/logs", exist_ok=True)
    with open(f"{cfg.out_dir}/logs/test_log.jsonl", "w", encoding="utf-8") as f:
        for lg in logs:
            f.write(json.dumps(lg, ensure_ascii=False, default=str) + "\n")
    print(pd.DataFrame(rows)["type"].value_counts())
    print("Đã ghi:", path, "|", len(rows), "dòng / tổng", len(ids), "câu | đã chạy model:", len(subs))
    problems = submission.validate_submission(path, ids)
    print("KIỂM ĐỊNH submission:", "OK" if not problems else problems[:10])
    if len(subs) < len(ids):
        print("Chưa chạy hết (limit_test/time_budget_s): câu thiếu được ghi abstain.")
    return subs, logs, problems
