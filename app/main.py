"""Điểm vào lúc nộp bài: python -m app.main  (đọc câu hỏi ở --test-dir, ghi submission.jsonl ở --out-dir)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.agent.pipeline import run_inference
from app.config import Config
from app.llm import create_backend


def add_common_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("--backend", choices=["local", "gemini"], default="local")
    p.add_argument("--query-engine", choices=["sql", "pandas"], default="sql")
    p.add_argument("--database-dir")
    p.add_argument("--ingestion-overrides", type=Path, help="JSON keyed by business id, shared with ingestion CLI")
    p.add_argument("--rebuild-database", action="store_true")
    p.add_argument("--no-rewrite", action="store_true")
    p.add_argument("--limit-train", type=int)
    p.add_argument("--train-dir")
    p.add_argument("--test-dir", default=os.environ.get("TEST_DIR"))
    p.add_argument("--out-dir", default=os.environ.get("OUT_DIR"))
    p.add_argument("--max-biz", type=int, help="số business train để chạy thử")
    p.add_argument("--per-biz", type=int, help="tối đa câu mỗi business")
    p.add_argument("--limit-test", type=int)
    p.add_argument("--time-budget", type=float, help="giây; hết giờ thì câu còn lại ghi abstain")
    p.add_argument("--n-samples", type=int)
    p.add_argument("--agree-min", type=int)
    p.add_argument("--gemini-model")
    p.add_argument("--local-model", default=os.environ.get("LOCAL_MODEL"))
    p.add_argument("--local-engine", choices=["auto", "vllm", "hf"])
    p.add_argument("--no-llm", action="store_true", help="không gọi model (SQL trả abstain; pandas dùng luật nếu bật)")
    p.add_argument("--exemplars", help="kho few-shot (.jsonl) dựng từ train bằng evaluation.run_eval --build-exemplars")
    p.add_argument("--no-hints", action="store_true", help="tắt gợi ý tất định (khoảng thời gian, giá trị khớp)")
    return p


def config_from_args(a: argparse.Namespace) -> Config:
    mapping = {"train_dir": a.train_dir, "test_dir": a.test_dir, "out_dir": a.out_dir, "max_biz": a.max_biz,
               "per_biz": a.per_biz, "limit_test": a.limit_test, "time_budget_s": a.time_budget,
               "n_samples": a.n_samples, "agree_min": a.agree_min, "gemini_model": a.gemini_model,
               "local_model": a.local_model, "local_engine": a.local_engine}
    mapping["exemplar_path"] = a.exemplars
    mapping.update(query_engine=a.query_engine, database_dir=a.database_dir,
                   ingestion_overrides=json.loads(a.ingestion_overrides.read_text()) if a.ingestion_overrides else {},
                   rebuild_database=a.rebuild_database, rewrite_questions=not a.no_rewrite,
                   limit_train=a.limit_train)
    return Config(backend=a.backend, use_hints=not a.no_hints, **{k: v for k, v in mapping.items() if v is not None})


def main(argv=None) -> int:
    parser = add_common_args(argparse.ArgumentParser(prog="app.main", description=__doc__))
    a = parser.parse_args(argv)
    cfg = config_from_args(a)
    backend = None if a.no_llm else create_backend(cfg)
    _subs, _logs, problems = run_inference(cfg, backend, use_llm=not a.no_llm)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
