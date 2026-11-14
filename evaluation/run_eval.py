"""Chạy một phần tập train rồi chấm PA bằng model local."""
from __future__ import annotations

import argparse
import json
import os

from app.agent.context import ContextBuilder
from app.agent.solver import Solver
from app.agent.sql_solver import SQLSolver
from app.config import Config
from app.ingestion.datasets.loader import DatasetLoader
from app.ingestion.datasets.splitting import BusinessSplitter
from app.llm import create_backend
from app.llm.base import LLMBackend
from app.main import add_common_args, config_from_args
from evaluation import scoring
from evaluation.report import save_summary, summarize


def run_train(cfg: Config, backend: LLMBackend | None, use_llm: bool = True, subset: str = "all", holdout_n: int = 6, calibration_n: int = 0):
    """Chạy một phần train (theo cfg.max_biz/per_biz) rồi chấm PA. Trả (solver, split, ev, subs, logs).

    subset: "all" | "fit" (trừ nhóm giữ lại, dùng để dựng kho ví dụ/SFT) | "holdout" (chỉ nhóm giữ lại, PA đáng tin)."""
    split = DatasetLoader.load_split(cfg.train_dir)
    if split.answers is None:
        raise ValueError("Evaluation cần answers.jsonl để đối chiếu")
    solver = SQLSolver(cfg, backend) if cfg.query_engine == "sql" else Solver(cfg, backend)
    ids = None
    if subset != "all":
        if calibration_n:
            groups = BusinessSplitter.development_groups(split.questions.business_id, holdout_n, calibration_n)
            ids = groups[subset]
        else:
            held = set(BusinessSplitter.holdout_businesses(split.questions.business_id, holdout_n))
            every = split.questions.business_id.unique()
            ids = [b for b in every if (b in held) == (subset == "holdout")]
    if cfg.query_engine == "sql" and solver.confidence:
        selected = set(ids if ids is not None else split.questions.business_id)
        if selected & set(solver.confidence.get('business_ids', [])):
            raise ValueError('Evaluation businesses overlap confidence calibration businesses')
    subs, logs = solver.solve_split(split, "train" if subset == "all" else f"train_{subset}", limit_biz=cfg.max_biz,
                                    per_biz=cfg.per_biz, use_llm=use_llm, business_ids=ids,
                                    limit_q=cfg.limit_train, time_budget=cfg.time_budget_s)
    ev = scoring.evaluate(subs, logs, split.answers)
    ev = ev.merge(split.questions[['id', 'business_id']], on='id', how='left', validate='one_to_one')
    print(scoring.summary_text(ev))
    summary = summarize(ev, logs, {"run_name": cfg.run_name or "default", "model": backend.tag() if backend else cfg.local_model,
                                  "subset": subset, "n_samples": cfg.n_samples, "agree_min": cfg.agree_min})
    print("đã lưu tóm tắt:", save_summary(summary, cfg.out_dir, f"{cfg.run_name or 'default'}_{subset}"))
    for by in ("source", "intent", "gold_type"):
        print(f"\nTheo {by}:")
        print(scoring.breakdown(ev, by).to_string())
    os.makedirs(f"{cfg.out_dir}/logs", exist_ok=True)
    with open(f"{cfg.out_dir}/logs/train_log.jsonl", "w", encoding="utf-8") as f:
        for lg in logs:
            f.write(json.dumps(lg, ensure_ascii=False, default=str) + "\n")
    ev.to_json(f"{cfg.out_dir}/evaluation.jsonl", orient="records", lines=True, force_ascii=False)
    with open(f"{cfg.out_dir}/predictions.jsonl", "w", encoding="utf-8") as f:
        for sub in subs:
            f.write(json.dumps(sub, ensure_ascii=False, allow_nan=False) + "\n")
    summary = {"questions": len(ev), "correct": int((ev.status == "correct").sum()),
               "wrong": int((ev.status == "wrong").sum()), "abstain": int((ev.status == "abstain").sum()),
               "PA": float(scoring.pa(ev)), "query_engine": cfg.query_engine,
               "model": backend.tag() if backend else None}
    with open(f"{cfg.out_dir}/summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    from collections import Counter
    analysis = {'by_business': json.loads(scoring.breakdown(ev, 'business_id').reset_index().to_json(orient='records')),
                'error_phases': dict(Counter(error.get('phase', 'unknown') for log in logs for error in log.get('errors', []))),
                'confidence_rejected': sum(bool(log.get('confidence_rejected')) for log in logs)}
    with open(f"{cfg.out_dir}/error_analysis.json", 'w', encoding='utf-8') as stream:
        json.dump(analysis, stream, ensure_ascii=False, indent=2, default=str)
    if (subset == "calibration" or (subset == "holdout" and not calibration_n)) and cfg.query_engine == "sql" and backend is not None:
        from evaluation.confidence import confidence_report, policy_signature
        from pathlib import Path
        Path(cfg.out_dir, 'confidence.json').write_text(json.dumps(
            {**confidence_report(ev, logs, backend.tag()), 'policy_signature': policy_signature(cfg, backend),
             'business_ids': sorted({log['business_id'] for log in logs if 'business_id' in log})},
            ensure_ascii=False, indent=2))
    return solver, split, ev, subs, logs


def main(argv=None) -> int:
    parser = add_common_args(argparse.ArgumentParser(prog="evaluation.run_eval", description=__doc__))
    parser.add_argument("--export-sft", action="store_true", help="ghi sft_train.jsonl từ lời giải đã đúng")
    parser.add_argument("--sft-all", action="store_true",
                        help="với --export-sft: lấy MỌI mẫu đúng (nên chạy kèm --collect-all) thay vì chỉ quyết định đồng thuận")
    parser.add_argument("--subset", choices=["all", "fit", "holdout"], default="all",
                        help="holdout = chỉ business giữ lại (đo PA khái quát); fit = phần còn lại (dựng kho ví dụ/SFT)")
    parser.add_argument("--holdout-n", type=int, default=6, help="số business giữ lại")
    parser.add_argument("--build-exemplars", metavar="PATH", help="ghi kho few-shot từ lời giải đã đúng (nên dùng với --subset fit)")
    a = parser.parse_args(argv)
    cfg = config_from_args(a)
    if cfg.query_engine == "sql" and (a.build_exemplars or a.export_sft):
        if a.subset != "fit":
            parser.error("SQL export requires --subset fit")
        cfg.collect_all = True
    backend = None if a.no_llm else create_backend(cfg)
    _solver, split, ev, _subs, logs = run_train(cfg, backend, use_llm=not a.no_llm, subset=a.subset,
                                                holdout_n=a.holdout_n)
    if cfg.query_engine == "sql" and (a.build_exemplars or a.export_sft):
        from training.sql_examples import export_sql_examples
        export_sql_examples(ev, logs, split, a.build_exemplars or f"{cfg.out_dir}/sql_examples.jsonl",
                            holdout_n=a.holdout_n,
                            sft_path=f"{cfg.out_dir}/sql_sft.jsonl" if a.export_sft else None)
        return 0
    if a.build_exemplars:
        from training.build_exemplars import build_exemplar_bank
        print(f"Đã ghi {build_exemplar_bank(ev, logs, split, a.build_exemplars)} ví dụ -> {a.build_exemplars}")
    if a.export_sft:
        from training.export_sft import export_sft
        n = export_sft(ev, logs, split, ContextBuilder(cfg, backend.count_tokens if backend else None),
                       f"{cfg.out_dir}/sft_train.jsonl", all_samples=a.sft_all)
        print(f"Đã ghi {n} mẫu SFT -> {cfg.out_dir}/sft_train.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
