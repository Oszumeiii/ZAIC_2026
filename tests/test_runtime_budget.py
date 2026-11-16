import json
from types import SimpleNamespace

import pytest

from app.config import Config
from app.llm.base import InferenceBudgetExceeded
from app.llm.local_service import LocalLLMService
from docker.audit import AuditedBackend
from docker.runtime import configure_runtime, question_budget, preparation_budget, ingest_before_deadline
from docker.runtime_report import summarize


def test_single_5090_profile_and_eager_fallback():
    cfg = Config(sql_review=True)
    assert configure_runtime(cfg, {'DATAMATE_PROFILE': 'rtx5090-6h'}) == 'rtx5090-6h'
    assert cfg.tensor_parallel == 1 and cfg.rewrite_questions
    assert cfg.max_retry == 1 and cfg.code_timeout_s == 20
    assert cfg.sql_review and (cfg.n_samples, cfg.agree_min) == (3, 2)
    assert cfg.max_model_len == 16384
    assert cfg.vllm_extra['enforce_eager'] is False
    assert cfg.vllm_extra['max_num_seqs'] == 4
    configure_runtime(cfg, {'DATAMATE_PROFILE': 'rtx5090-6h', 'DATAMATE_ENFORCE_EAGER': '1'})
    assert cfg.vllm_extra['enforce_eager'] is True


def test_default_two_gpu_profile_preserves_rewrite():
    cfg = Config(sql_review=True, rewrite_questions=False)
    assert configure_runtime(cfg, {}) == 'rtx5090-2gpu-6h'
    assert cfg.tensor_parallel == 2 and cfg.rewrite_questions
    assert cfg.max_retry == 1 and cfg.sql_review and cfg.agree_min == 2
    assert cfg.vllm_extra['enforce_eager'] is False


@pytest.mark.parametrize('environ', [
    {'DATAMATE_PROFILE': 'invalid'},
    {'DATAMATE_PROFILE': 'rtx5090-6h', 'DATAMATE_TENSOR_PARALLEL': '2'},
    {'DATAMATE_PROFILE': 'rtx5090-2gpu-6h', 'DATAMATE_TENSOR_PARALLEL': '1'},
    {'DATAMATE_ENFORCE_EAGER': 'yes'},
])
def test_invalid_runtime_settings_are_rejected(environ):
    with pytest.raises(ValueError):
        configure_runtime(Config(), environ)


def test_fair_time_allocation_reserves_future_preparation_and_redistributes():
    assert question_budget(1000, 10, 2, 100) == 80
    assert question_budget(950, 9, 2, 100) == pytest.approx(750 / 9)
    assert question_budget(1000, 10, 2, 10000) == 50  # reserve capped at half
    assert question_budget(1000, 1, 0, 0) == 120
    assert question_budget(-1, 1, 0, 0) == 0


def test_600_question_budget_includes_startup_and_ingestion():
    # 5h55 worker budget minus 10m startup and 60m preparation, shared over 600 questions.
    assert question_budget(20700, 600, 29, 10, preparation_reserve_s=3600) == 28.5
    # Once the last business is ready, unused preparation time is available for questions.
    assert question_budget(1000, 10, 0, 10, preparation_reserve_s=300) == 100
    assert preparation_budget(20700, 3600, 30) == 240
    assert preparation_budget(100, 3600, 1) == 100
    assert preparation_budget(100, -1, 10) == 0


def test_simulated_600_questions_share_one_end_to_end_deadline():
    elapsed, preparation_used, answered = 600.0, 0.0, 0
    for business_index in range(30):
        allocation = preparation_budget(21300 - elapsed, 3600 - preparation_used, 30 - business_index)
        assert allocation >= 120
        elapsed += 120
        preparation_used += 120
        for _ in range(20):
            allocation = question_budget(21300 - elapsed, 600 - answered, 29 - business_index, 120,
                                         preparation_reserve_s=3600 - preparation_used)
            assert allocation == pytest.approx(28.5)
            elapsed += allocation
            answered += 1
    assert answered == 600 and elapsed == pytest.approx(21300)


def test_ingestion_timeout_rejects_partial_database(tmp_path, monkeypatch):
    import subprocess
    from app.ingestion.catalog import CatalogService

    def expired(command, **kwargs):
        assert command[1:3] == ['-m', 'app.ingestion.processing.pipeline']
        assert kwargs == {'check': True, 'timeout': 5}
        raise subprocess.TimeoutExpired(command, 5)

    monkeypatch.setattr('time.monotonic', lambda: 100)
    monkeypatch.setattr(subprocess, 'run', expired)
    monkeypatch.setattr(CatalogService, 'read_catalog', lambda *args: pytest.fail('Partial DB was read'))
    with pytest.raises(InferenceBudgetExceeded, match='during ingestion'):
        ingest_before_deadline(tmp_path, tmp_path, tmp_path/'manifest', tmp_path/'rules', 105)
    with pytest.raises(InferenceBudgetExceeded, match='before ingestion'):
        ingest_before_deadline(tmp_path, tmp_path, tmp_path/'manifest', tmp_path/'rules', 100)


def test_model_call_budget_includes_invalid_json_retries():
    calls = []
    def generate(*args, **kwargs):
        calls.append(kwargs)
        return ['invalid json']
    transport = SimpleNamespace(generate_with_options=generate, prompt_tokens=lambda *args: 1)
    audited = AuditedBackend(transport)
    audited.set_budget(max_calls=2)
    service = LocalLLMService(audited, Config(max_retry=5))
    with pytest.raises(InferenceBudgetExceeded, match='model-call'):
        service.json('s', 'p', {'type': 'object'})
    assert len(calls) == len(audited.calls) == 2
    audited.set_budget(max_calls=1)
    audited.generate_with_options('s', 'p')
    assert len(calls) == 3


def test_deadline_stops_next_generation_without_interrupting_inflight(monkeypatch):
    now = [0.0]
    monkeypatch.setattr('docker.audit.time.monotonic', lambda: now[0])
    def generate(*args, **kwargs):
        now[0] = 20.0
        return ['{}']
    audited = AuditedBackend(SimpleNamespace(generate_with_options=generate))
    audited.set_budget(deadline=10)
    assert audited.generate_with_options('s', 'p') == ['{}']
    with pytest.raises(InferenceBudgetExceeded, match='time budget'):
        audited.generate_with_options('s', 'p')
    assert len(audited.calls) == 1


def test_runtime_report_counts_shared_preparation_once(tmp_path):
    logs = tmp_path / 'logs'
    logs.mkdir()
    events = [
        {'stage': 'shared_data', 'status': 'completed', 'elapsed_s': 100, 'questions': 10, 'businesses': 2},
        {'stage': 'manifest', 'status': 'completed', 'business': 'b1', 'duration_s': 30},
        {'stage': 'ingestion', 'status': 'completed', 'business': 'b1', 'duration_s': 10},
        {'stage': 'question', 'status': 'completed', 'answer_type': 'number', 'duration_s': 20},
        {'stage': 'question', 'status': 'completed', 'answer_type': 'abstain', 'duration_s': 40,
         'budget_exhausted': True},
    ]
    (logs / 'progress.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events) + '{')
    (logs / 'run.json').write_text(json.dumps({'time_budget_s': 600, 'profile': 'rtx5090-6h'}))
    report = summarize(tmp_path)
    assert report['projected_total_s'] == 480
    assert report['target_mean_question_s'] == 42
    assert report['answered_questions'] == report['budget_exhausted_questions'] == 1
    assert report['projected_within_budget'] is True
    events.append({'stage': 'preparation', 'status': 'completed', 'business': 'b1', 'duration_s': 50})
    (logs / 'progress.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    (logs / 'run.json').write_text(json.dumps({'time_budget_s': 600, 'preparation_budget_s': 200}))
    report = summarize(tmp_path)
    assert report['mean_preparation_s'] == 50
    assert report['projected_total_s'] == 500  # includes context, not 50+30+10
    assert report['planned_target_mean_question_s'] == 30
