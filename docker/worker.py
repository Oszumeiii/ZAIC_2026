"""Competition entrypoint: offline, incremental answers and per-question audit logs."""
import hashlib
import json
import os
import random
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from docker.audit import AuditedBackend, record_sql
from docker.runtime import (FAST_PROFILES, PREPARATION_BUDGET_S, QUESTION_MAX_CALLS,
                            configure_runtime, question_budget, preparation_budget, ingest_before_deadline)
from app.llm.base import InferenceBudgetExceeded
from app.submission import make_sub, validate_answer, validate_submission, write_submission


def json_safe(value):
    import math
    if isinstance(value, float) and not math.isfinite(value):
        return {'nonfinite_float': repr(value)}
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(json_safe(value), stream, ensure_ascii=False, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ProgressLog:
    """Compact, durable stage events for SSH monitoring and post-run diagnosis."""
    def __init__(self, output, started):
        self.path = Path(output) / 'logs/progress.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.started = started

    def write(self, stage, status, **details):
        # Keep the global timeline distinct from an individual stage's duration.
        if 'elapsed_s' in details:
            details['duration_s'] = details.pop('elapsed_s')
        event = {'time_utc': datetime.now(timezone.utc).isoformat(),
                 'elapsed_s': round(time.monotonic() - self.started, 3),
                 'stage': stage, 'status': status, **details}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(json_safe(event), ensure_ascii=False, default=str, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        summary = ', '.join(f'{key}={value}' for key, value in details.items()
                            if key not in ('error', 'answer'))
        if 'error' in details:
            summary += f", error={details['error']}"
        print(f"[pipeline +{event['elapsed_s']:.1f}s] {stage}: {status}"
              f"{', ' + summary if summary else ''}", flush=True)


class OutputJournal:
    def __init__(self, questions, output):
        self.output, self.questions = Path(output), questions
        self.ids = [q['id'] for q in questions]
        if len(set(self.ids)) != len(self.ids):
            raise ValueError('Duplicate question ids')
        self.positions = {qid: i for i, qid in enumerate(self.ids)}
        self.rows = {qid: make_sub(qid, 'abstain', None) for qid in self.ids}
        self.output.mkdir(parents=True, exist_ok=True)
        write_submission([], self.ids, str(self.output / 'submission.jsonl'))
        for qid in self.ids:
            self.log(qid, {'status': 'pending', 'elapsed_s': 0.0})

    def log(self, qid, details):
        thinking = [thought for call in details.get('model_calls', []) for thought in call.get('thinking', [])]
        executions = details.get('execution_trace', [])
        required = {'answer': {k:v for k,v in self.rows[qid].items() if k != 'id'},
                    'elapsed_ms': max(0, int(details.get('elapsed_s', 0) * 1000)),
                    'thinking': thinking or None,
                    'code': [e['code'] for e in executions] or None,
                    'output': [e.get('output') for e in executions] or None}
        atomic_json(self.output / 'logs' / f"question_{self.positions[qid]:06d}.json",
                    {**details, **required, 'id': qid, 'final_answer': self.rows[qid]})

    def record(self, row, details):
        validate_answer(row)
        self.rows[row['id']] = row
        self.log(row['id'], details)
        write_submission(list(self.rows.values()), self.ids, str(self.output / 'submission.jsonl'))


def seed_for(question, seed=2026):
    # Seed from question content, never hard-coded question/business answer mappings.
    text = json.dumps([seed, question['question'], question['as_of_date']], ensure_ascii=False)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], 'big')


def gpu_preflight(torch, tensor_parallel):
    if not torch.cuda.is_available() or torch.cuda.device_count() < tensor_parallel:
        raise RuntimeError(f'Worker requires {tensor_parallel} CUDA GPU(s); '
                           'set DATAMATE_TENSOR_PARALLEL=1 for a single GPU')
    cuda = tuple(int(v) for v in torch.version.cuda.split('.')[:2])
    if cuda < (12, 8):
        raise RuntimeError('Worker image requires CUDA >= 12.8')
    hardware = {'torch': torch.__version__, 'cuda': torch.version.cuda,
                'tensor_parallel': tensor_parallel,
                'devices': [torch.cuda.get_device_name(i) for i in range(tensor_parallel)],
                'compute_capability': [torch.cuda.get_device_capability(i) for i in range(tensor_parallel)],
                'peer_access_0_1': torch.cuda.can_device_access_peer(0, 1) if tensor_parallel > 1 else None}
    for index in range(tensor_parallel):
        probe = torch.ones((32, 32), dtype=torch.bfloat16, device=f'cuda:{index}')
        if (probe @ probe).float().sum().item() != 32768:
            raise RuntimeError(f'CUDA BF16 preflight failed on GPU {index}')
    return hardware


def run(data, output, model, *, seconds=21300, backend_factory=None, verify_weights=True):
    started = time.monotonic()
    deadline = started + seconds
    data, output, model = Path(data).resolve(), Path(output).resolve(), Path(model).resolve()
    if output == data or data in output.parents:
        raise ValueError('Output must be outside the read-only dataset')
    progress = ProgressLog(output, started)
    progress.write('input', 'started')
    questions = [json.loads(line) for line in (data / 'questions.jsonl').read_text().splitlines() if line.strip()]
    journal = OutputJournal(questions, output)
    progress.write('input', 'completed', questions=len(questions))
    progress.write('imports', 'started')
    import pandas as pd
    from app.config import Config
    from app.agent.cache import ResultCache
    from app.agent.sql_solver import SQLSolver
    from app.ingestion.datasets.loader import Split
    from app.ingestion.layered import BusinessIngestion
    from app.ingestion.processing.pipeline import IngestionPipeline
    from app.llm import create_backend
    from app.llm.local_service import LocalLLMService
    from app.manifests.local import LocalManifestGenerator
    from docker.verify_model import verify
    progress.write('imports', 'completed')
    with tempfile.TemporaryDirectory(prefix='zaic_worker_') as workspace:
        work = Path(workspace)
        cfg = Config(local_model=str(model), local_engine='vllm', tensor_parallel=2,
                     max_model_len=16384, max_new_tokens=2048, max_retry=5, n_samples=3,
                     agree_min=2, sql_review=True, require_prepared_database=True,
                     chat_kwargs={'enable_thinking': False}, vllm_extra={'enforce_eager': True, 'seed': 2026, 'disable_custom_all_reduce': True},
                     database_dir=str(work / 'databases'), manifest_dir=str(work / 'manifests'),
                     out_dir=str(output), test_dir=str(data))
        try:
            profile = configure_runtime(cfg, os.environ)
            preparation_limit_s = min(PREPARATION_BUDGET_S, max(0.0, seconds) * 0.2)
            limit_preparation = profile == 'rtx5090-2gpu-6h'
            max_calls = QUESTION_MAX_CALLS if profile in FAST_PROFILES else None
            progress.write('model_verification', 'started')
            manifest = verify(model) if verify_weights else {'test_fixture': True}
            progress.write('model_verification', 'completed', parameters=manifest.get('parameters'))
            if time.monotonic() >= deadline:
                progress.write('deadline', 'reached', stage_at_timeout='model_verification')
                return 0
            hardware = {"test_backend": backend_factory is not None}
            if backend_factory is None:
                import torch
                progress.write('gpu_preflight', 'started', tensor_parallel=cfg.tensor_parallel)
                hardware = gpu_preflight(torch, cfg.tensor_parallel)
                progress.write('gpu_preflight', 'completed', devices=hardware['devices'])
                torch.manual_seed(2026)
                torch.cuda.manual_seed_all(2026)
            random.seed(2026)
            import numpy as np
            np.random.seed(2026)
            atomic_json(output / 'logs/run.json', {'config': asdict(cfg), 'model': manifest,
                        'hardware': hardware, 'profile': profile, 'time_budget_s': seconds,
                        'question_max_calls': max_calls,
                        'preparation_budget_s': preparation_limit_s if limit_preparation else None,
                        'question_count': len(questions),
                        'status': 'loading', 'started_unix': time.time()})
            progress.write('model_loading', 'started', engine=cfg.local_engine,
                           tensor_parallel=cfg.tensor_parallel)
            backend = AuditedBackend((backend_factory or create_backend)(cfg))
            backend.set_budget(deadline=deadline)
            progress.write('model_loading', 'completed')
            llm = LocalLLMService(backend, cfg, manifest_tokens=6144)
            generator = LocalManifestGenerator(llm)
            progress.write('shared_data', 'started')
            rules = (data / 'rulebook.md').read_text(encoding='utf-8-sig')
            calendar = BusinessIngestion.ingest_calendar(data / 'calendar.csv', rulebook=rules).frames['calendar']
            # Never load answers.jsonl, even if supplied by a development mount.
            split = Split(str(data), pd.DataFrame(questions), None, rules, calendar)
            solver = SQLSolver(cfg, llm)
            cache = ResultCache(str(work / 'question_cache.jsonl'))
            groups = {}
            for q in questions:
                groups.setdefault(q['business_id'], []).append(q)
            progress.write('shared_data', 'completed', businesses=len(groups), questions=len(questions))
            completed_questions = 0
            preparation_times = []
            preparation_used_s = 0.0
            preparation_failed_businesses = 0
            preparation_budget_exhausted_businesses = 0
            budget_exhausted_questions = 0
            for business_index, (business_id, rows) in enumerate(groups.items(), 1):
                if time.monotonic() >= deadline:
                    progress.write('deadline', 'reached', completed_questions=completed_questions)
                    break
                business_started = time.monotonic()
                backend.calls = []
                preparation_deadline = deadline
                if limit_preparation:
                    allocated_preparation_s = preparation_budget(deadline - business_started,
                        preparation_limit_s - preparation_used_s, len(groups) - business_index + 1)
                    preparation_deadline = min(deadline, business_started + allocated_preparation_s)
                backend.set_budget(deadline=preparation_deadline)
                business = data / 'data' / business_id
                progress.write('business', 'started', business=business_id,
                               index=business_index, total=len(groups), questions=len(rows),
                               preparation_budget_s=max(0.0, preparation_deadline - business_started))
                try:
                    backend.check_budget()
                    if business.resolve().parent != data / 'data':
                        raise ValueError('Invalid business path')
                    if hasattr(backend, 'reset_seed'):
                        backend.reset_seed(2026)
                    progress.write('manifest', 'started', business=business_id)
                    mapped = generator.generate(business, rules, cfg.manifest_dir)
                    progress.write('manifest', 'completed', business=business_id,
                                   elapsed_s=round(time.monotonic() - business_started, 3))
                    if time.monotonic() >= deadline:
                        progress.write('deadline', 'reached', stage_at_timeout='manifest', business=business_id)
                        break
                    backend.check_budget()
                    ingestion_started = time.monotonic()
                    progress.write('ingestion', 'started', business=business_id)
                    ingestion_output = Path(cfg.database_dir) / business_id / 'current'
                    if limit_preparation:
                        catalog = ingest_before_deadline(business, ingestion_output, mapped,
                                                         data / 'rulebook.md', preparation_deadline)
                    else:
                        catalog = IngestionPipeline.ingest(business, ingestion_output,
                                                           mapped, rulebook=rules, mode='production')
                    progress.write('ingestion', 'completed', business=business_id,
                                   elapsed_s=round(time.monotonic() - ingestion_started, 3))
                    preparation_queries = []
                    if limit_preparation:
                        backend.check_budget()
                        context_started = time.monotonic()
                        progress.write('context', 'started', business=business_id)
                        with record_sql() as preparation_queries:
                            solver.builder.prepare(split, business_id)
                        backend.check_budget()
                        progress.write('context', 'completed', business=business_id,
                                       elapsed_s=round(time.monotonic() - context_started, 3))
                    preparation_s = time.monotonic() - business_started
                    preparation_times.append(preparation_s)
                    progress.write('preparation', 'completed', business=business_id,
                                   elapsed_s=round(preparation_s, 3))
                    preparation_calls = list(backend.calls)
                    preparation_execution = {'code': 'app/ingestion/processing/pipeline.py::IngestionPipeline.ingest',
                        'inputs': {'business_dir': str(business), 'output_dir': str(Path(cfg.database_dir) / business_id / 'current'),
                                   'manifest_path': str(mapped), 'rulebook': rules, 'mode': 'production'},
                        'output': catalog}
                except Exception as error:
                    preparation_failed_businesses += 1
                    preparation_budget_exhausted_businesses += int(isinstance(error, InferenceBudgetExceeded))
                    progress.write('business', 'preparation_failed', business=business_id,
                                   error=repr(error), budget_exhausted=isinstance(error, InferenceBudgetExceeded),
                                   elapsed_s=round(time.monotonic() - business_started, 3))
                    for q in rows:
                        journal.record(make_sub(q['id'], 'abstain', None), {'status': 'preparation_failed',
                            'elapsed_s': time.monotonic() - business_started, 'model_calls': list(backend.calls), 'error': repr(error)})
                        completed_questions += 1
                    continue
                finally:
                    preparation_used_s += time.monotonic() - business_started
                business_question_start = completed_questions
                for q in rows:
                    if time.monotonic() >= deadline:
                        progress.write('deadline', 'reached', completed_questions=completed_questions)
                        break
                    question_started = time.monotonic()
                    backend.calls = []
                    remaining_s = max(0.0, deadline - question_started)
                    allocated_s = remaining_s
                    if profile in FAST_PROFILES:
                        allocated_s = question_budget(remaining_s, len(questions) - completed_questions,
                            len(groups) - business_index, sum(preparation_times) / len(preparation_times),
                            preparation_reserve_s=(max(0, preparation_limit_s - preparation_used_s)
                                                   if limit_preparation else None))
                    backend.set_budget(deadline=min(deadline, question_started + allocated_s),
                                       max_calls=max_calls)
                    seed = seed_for(q)
                    if hasattr(backend, 'reset_seed'):
                        backend.reset_seed(seed)
                    journal.log(q['id'], {'status': 'running', 'elapsed_s': 0.0, 'seed': seed,
                                         'preparation_s': preparation_s})
                    progress.write('question', 'started', business=business_id, id=q['id'],
                                   index=completed_questions + 1, total=len(questions),
                                   budget_s=round(allocated_s, 3), remaining_s=round(remaining_s, 3))
                    executions = []
                    try:
                        with record_sql() as executions:
                            result, log = solver._solve_row(SimpleNamespace(**q), split, cache, True)
                    except Exception as error:
                        result, log = make_sub(q['id'], 'abstain', None), {
                            'error': repr(error), 'budget_exhausted': isinstance(error, InferenceBudgetExceeded)}
                    budget_exhausted_questions += int(log.get('budget_exhausted', False))
                    journal.record(result, {**log,
                                   'status': 'budget_exhausted' if log.get('budget_exhausted') else 'completed',
                                   'seed': seed, 'question_budget_s': allocated_s,
                                   'preparation_s': preparation_s, 'question_s': time.monotonic() - question_started,
                                   'model_calls': preparation_calls + backend.calls,
                                   'execution_trace': [preparation_execution] + preparation_queries + executions,
                                   'elapsed_s': time.monotonic() - business_started})
                    completed_questions += 1
                    progress.write('question', 'completed', business=business_id, id=q['id'],
                                   answer_type=result['type'],
                                   budget_exhausted=log.get('budget_exhausted', False),
                                   model_calls=len(backend.calls),
                                   elapsed_s=round(time.monotonic() - question_started, 3),
                                   completed_questions=completed_questions, total=len(questions))
                progress.write('business', 'completed' if completed_questions - business_question_start == len(rows) else 'partial', business=business_id,
                               elapsed_s=round(time.monotonic() - business_started, 3))
            atomic_json(output / 'logs/completion.json', {'elapsed_s': time.monotonic() - started,
                        'deadline_reached': time.monotonic() >= deadline,
                        'completed_questions': completed_questions, 'total_questions': len(questions),
                        'answered_questions': sum(row['type'] != 'abstain' for row in journal.rows.values()),
                        'budget_exhausted_questions': budget_exhausted_questions,
                        'preparation_s': preparation_used_s,
                        'preparation_failed_businesses': preparation_failed_businesses,
                        'preparation_budget_exhausted_businesses': preparation_budget_exhausted_businesses})
            errors = validate_submission(str(output / 'submission.jsonl'), journal.ids)
            progress.write('submission', 'completed' if not errors else 'invalid',
                           completed_questions=completed_questions, total=len(questions),
                           elapsed_s=round(time.monotonic() - started, 3), errors=len(errors))
            return int(bool(errors))
        except Exception as error:
            progress.write('pipeline', 'fatal', error=repr(error))
            atomic_json(output / 'logs/fatal.json', {'error': repr(error), 'elapsed_s': time.monotonic() - started})
            raise


if __name__ == '__main__':
    raise SystemExit(run('/data', '/result', '/code/models/Qwen3.5-9B'))
