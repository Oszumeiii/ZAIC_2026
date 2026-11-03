"""Chạy một phần tập train rồi chấm PA: python -m evaluation.run_eval --backend gemini --max-biz 2 --per-biz 15"""
from __future__ import annotations

import argparse
import json
import os

from app.agent.context import ContextBuilder
from app.agent.solver import Solver
from app.config import Config
from app.ingestion.dataset import load_split
from app.llm import create_backend
from app.llm.base import LLMBackend
from app.main import add_common_args, config_from_args
from evaluation import scoring


def run_train(cfg: Config, backend: LLMBackend | None, use_llm: bool = True):
    """Chạy một phần train (theo cfg.max_biz/per_biz) rồi chấm PA. Trả (solver, split, ev, subs, logs)."""
    split = load_split(cfg.train_dir)
    solver = Solver(cfg, backend)
    subs, logs = solver.solve_split(split, "train", limit_biz=cfg.max_biz, per_biz=cfg.per_biz, use_llm=use_llm)
    ev = scoring.evaluate(subs, logs, split.answers)
    print(scoring.summary_text(ev))
    for by in ("source", "intent", "gold_type"):
        print(f"\nTheo {by}:")
        print(scoring.breakdown(ev, by).to_string())
    os.makedirs(f"{cfg.out_dir}/logs", exist_ok=True)
    with open(f"{cfg.out_dir}/logs/train_log.jsonl", "w", encoding="utf-8") as f:
        for lg in logs:
            f.write(json.dumps(lg, ensure_ascii=False, default=str) + "\n")
    return solver, split, ev, subs, logs


def main(argv=None) -> int:
    parser = add_common_args(argparse.ArgumentParser(prog="evaluation.run_eval", description=__doc__))
    parser.add_argument("--export-sft", action="store_true", help="ghi sft_train.jsonl từ lời giải đã đúng")
    a = parser.parse_args(argv)
    cfg = config_from_args(a)
    backend = None if a.no_llm else create_backend(cfg)
    _solver, split, ev, _subs, logs = run_train(cfg, backend, use_llm=not a.no_llm)
    if a.export_sft:
        from training.export_sft import export_sft
        n = export_sft(ev, logs, split, ContextBuilder(cfg, backend.count_tokens if backend else None),
                       f"{cfg.out_dir}/sft_train.jsonl")
        print(f"Đã ghi {n} mẫu SFT -> {cfg.out_dir}/sft_train.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
