"""Suy luận trên một split không cần đáp án: ghi submission.jsonl đủ mọi id, có kiểm định."""
from __future__ import annotations

import json
import os
import tempfile

import pandas as pd

from app import submission
from app.agent.solver import Solver
from app.agent.sql_solver import SQLSolver
from app.config import Config
from app.ingestion.datasets.loader import DatasetLoader
from app.llm.base import LLMBackend


def run_inference(cfg: Config, backend: LLMBackend | None, use_llm: bool = True):
    """Dự đoán split ở cfg.test_dir, ghi submission.jsonl + log. Trả (subs, logs, problems)."""
    split = DatasetLoader.load_split(cfg.test_dir)
    solver = SQLSolver(cfg, backend) if cfg.query_engine == "sql" else Solver(cfg, backend)
    ids = list(split.questions["id"])
    path = f"{cfg.out_dir}/submission.jsonl"
    log_path = f"{cfg.out_dir}/logs/test_log.jsonl"
    os.makedirs(f"{cfg.out_dir}/logs", exist_ok=True)
    submission.write_submission([], ids, path)

    def checkpoint(current_subs, current_logs):
        submission.write_submission(current_subs, ids, path)
        directory = os.path.dirname(log_path)
        descriptor, temporary_path = tempfile.mkstemp(prefix=".test_log.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                for item in current_logs:
                    stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
            os.replace(temporary_path, log_path)
        except Exception:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise

    subs, logs = solver.solve_split(split, "test", limit_q=cfg.limit_test, use_llm=use_llm,
                                    time_budget=cfg.time_budget_s, progress_callback=checkpoint)
    checkpoint(subs, logs)
    rows = submission.fill_missing(subs, ids)
    print(pd.DataFrame(rows)["type"].value_counts())
    print("Đã ghi:", path, "|", len(rows), "dòng / tổng", len(ids), "câu | đã chạy model:", len(subs))
    problems = submission.validate_submission(path, ids)
    print("KIỂM ĐỊNH submission:", "OK" if not problems else problems[:10])
    if len(subs) < len(ids):
        print("Chưa chạy hết (limit_test/time_budget_s): câu thiếu được ghi abstain.")
    return subs, logs, problems
