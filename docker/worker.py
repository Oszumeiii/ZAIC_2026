"""Competition entrypoint: offline, incremental answers and per-question audit logs."""
import hashlib
import json
import os
import random
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from docker.audit import AuditedBackend, record_sql
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


def run(data, output, model, *, seconds=21300, backend_factory=None, verify_weights=True):
    started = time.monotonic()
    deadline = started + seconds
    data, output, model = Path(data).resolve(), Path(output).resolve(), Path(model).resolve()
    if output == data or data in output.parents:
        raise ValueError('Output must be outside the read-only dataset')
    questions = [json.loads(line) for line in (data / 'questions.jsonl').read_text().splitlines() if line.strip()]
    journal = OutputJournal(questions, output)
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
    with tempfile.TemporaryDirectory(prefix='zaic_worker_') as workspace:
        work = Path(workspace)
        cfg = Config(local_model=str(model), local_engine='vllm', tensor_parallel=2,
                     max_model_len=16384, max_new_tokens=2048, max_retry=5, n_samples=3,
                     agree_min=2, sql_review=True, require_prepared_database=True,
                     chat_kwargs={'enable_thinking': False}, vllm_extra={'enforce_eager': True, 'seed': 2026, 'disable_custom_all_reduce': True},
                     database_dir=str(work / 'databases'), manifest_dir=str(work / 'manifests'),
                     out_dir=str(output), test_dir=str(data))
        try:
            manifest = verify(model) if verify_weights else {'test_fixture': True}
            if time.monotonic() >= deadline:
                return 0
            hardware = {"test_backend": backend_factory is not None}
            if backend_factory is None:
                import torch
                if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
                    raise RuntimeError('Frozen worker requires two CUDA GPUs')
                cuda = tuple(int(v) for v in torch.version.cuda.split('.')[:2])
                if cuda < (12, 8):
                    raise RuntimeError('Blackwell requires CUDA >= 12.8')
                hardware = {"torch": torch.__version__, "cuda": torch.version.cuda,
                            "devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]}
                hardware['compute_capability'] = [torch.cuda.get_device_capability(i) for i in range(2)]
                hardware['peer_access_0_1'] = torch.cuda.can_device_access_peer(0, 1)
                # Execute a real BF16 kernel on both devices before expensive model loading.
                for index in range(2):
                    probe = torch.ones((32, 32), dtype=torch.bfloat16, device=f'cuda:{index}')
                    if (probe @ probe).float().sum().item() != 32768:
                        raise RuntimeError(f'CUDA BF16 preflight failed on GPU {index}')
                    del probe
                torch.manual_seed(2026)
                torch.cuda.manual_seed_all(2026)
            random.seed(2026)
            import numpy as np
            np.random.seed(2026)
            atomic_json(output / 'logs/run.json', {'config': asdict(cfg), 'model': manifest,
                        'hardware': hardware, 'time_budget_s': seconds, 'status': 'loading', 'started_unix': time.time()})
            backend = AuditedBackend((backend_factory or create_backend)(cfg))
            llm = LocalLLMService(backend, cfg, manifest_tokens=6144)
            generator = LocalManifestGenerator(llm)
            rules = (data / 'rulebook.md').read_text(encoding='utf-8-sig')
            calendar = BusinessIngestion.ingest_calendar(data / 'calendar.csv', rulebook=rules).frames['calendar']
            # Never load answers.jsonl, even if supplied by a development mount.
            split = Split(str(data), pd.DataFrame(questions), None, rules, calendar)
            solver = SQLSolver(cfg, llm)
            cache = ResultCache(str(work / 'question_cache.jsonl'))
            groups = {}
            for q in questions:
                groups.setdefault(q['business_id'], []).append(q)
            for business_id, rows in groups.items():
                if time.monotonic() >= deadline:
                    break
                business_started = time.monotonic()
                backend.calls = []
                business = data / 'data' / business_id
                try:
                    if business.resolve().parent != data / 'data':
                        raise ValueError('Invalid business path')
                    if hasattr(backend, 'reset_seed'):
                        backend.reset_seed(2026)
                    mapped = generator.generate(business, rules, cfg.manifest_dir)
                    if time.monotonic() >= deadline:
                        break
                    catalog = IngestionPipeline.ingest(business, Path(cfg.database_dir) / business_id / 'current',
                                             mapped, rulebook=rules, mode='production')
                    preparation_s = time.monotonic() - business_started
                    preparation_calls = list(backend.calls)
                    preparation_execution = {'code': 'app/ingestion/processing/pipeline.py::IngestionPipeline.ingest',
                        'inputs': {'business_dir': str(business), 'output_dir': str(Path(cfg.database_dir) / business_id / 'current'),
                                   'manifest_path': str(mapped), 'rulebook': rules, 'mode': 'production'},
                        'output': catalog}
                except Exception as error:
                    for q in rows:
                        journal.record(make_sub(q['id'], 'abstain', None), {'status': 'preparation_failed',
                            'elapsed_s': time.monotonic() - business_started, 'model_calls': list(backend.calls), 'error': repr(error)})
                    continue
                for q in rows:
                    if time.monotonic() >= deadline:
                        break
                    question_started = time.monotonic()
                    backend.calls = []
                    seed = seed_for(q)
                    if hasattr(backend, 'reset_seed'):
                        backend.reset_seed(seed)
                    journal.log(q['id'], {'status': 'running', 'elapsed_s': 0.0, 'seed': seed,
                                         'preparation_s': preparation_s})
                    executions = []
                    try:
                        with record_sql() as executions:
                            result, log = solver._solve_row(SimpleNamespace(**q), split, cache, True)
                    except Exception as error:
                        result, log = make_sub(q['id'], 'abstain', None), {'error': repr(error)}
                    journal.record(result, {**log, 'status': 'completed', 'seed': seed,
                                   'preparation_s': preparation_s, 'question_s': time.monotonic() - question_started,
                                   'model_calls': preparation_calls + backend.calls,
                                   'execution_trace': [preparation_execution] + executions,
                                   'elapsed_s': time.monotonic() - business_started})
            atomic_json(output / 'logs/completion.json', {'elapsed_s': time.monotonic() - started,
                        'deadline_reached': time.monotonic() >= deadline})
            return int(bool(validate_submission(str(output / 'submission.jsonl'), journal.ids)))
        except Exception as error:
            atomic_json(output / 'logs/fatal.json', {'error': repr(error), 'elapsed_s': time.monotonic() - started})
            raise


if __name__ == '__main__':
    raise SystemExit(run('/data', '/result', '/code/models/Qwen3.5-9B'))
