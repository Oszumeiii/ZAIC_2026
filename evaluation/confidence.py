"""Held-out vote precision with Wilson lower bounds; no gold at inference."""
import math


def confidence_report(ev, logs, model):
    by_id = {r['id']: r for r in logs}
    groups = {}
    for row in ev.itertuples():
        if row.status == 'abstain':
            continue
        key = f'{row.type}:{by_id[row.id]["agree"]}'
        bucket = groups.setdefault(key, {'n': 0, 'correct': 0})
        bucket['n'] += 1
        bucket['correct'] += row.status == 'correct'
    for bucket in groups.values():
        n, c = bucket['n'], bucket['correct']
        p, z = c/n, 1.96
        bucket['precision'] = p
        bucket['lower_precision'] = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n)
    return {'version': 1, 'model': model, 'buckets': groups,
            'note': 'Calibration data; measure final PA on separate unseen businesses.'}


def policy_signature(cfg, backend):
    import hashlib
    import json
    from pathlib import Path
    from app.agent.sql_solver import SQL_SYSTEM, SQL_SCHEMA
    from app.agent.sql_review import REVIEW_SYSTEM, REVIEW_SCHEMA
    from app.ingestion.catalog import PIPELINE_VERSION
    from app.llm.local_service import LocalLLMService
    fields = ('n_samples', 'agree_min', 'min_agree_by_type', 'max_retry', 'sql_review',
              'collect_all', 'temperature', 'rewrite_questions', 'use_hints', 'exemplar_k', 'max_model_len', 'max_new_tokens')
    payload = {k: getattr(cfg, k) for k in fields}
    payload.update(model=backend.tag(), sql=SQL_SYSTEM, review=REVIEW_SYSTEM,
                   sql_schema=SQL_SCHEMA, review_schema=REVIEW_SCHEMA, ingestion=PIPELINE_VERSION,
                   local_service=LocalLLMService.VERSION,
                   examples=Path(cfg.exemplar_path).read_text() if cfg.exemplar_path else '')
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
