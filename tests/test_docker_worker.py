import hashlib
import json
import struct
from pathlib import Path

import pytest
from app.manifests.service import BusinessManifestService
from app.submission import validate_submission
from docker.worker import OutputJournal, run, seed_for
from docker.verify_model import inventory, verify
from evaluation.fakes import write_split


class Transport:
    def __init__(self, manifest):
        self.manifest = manifest
        self.seeds = []

    def tag(self):
        return 'offline-fixture'

    def reset_seed(self, seed):
        self.seeds.append(seed)

    def count_tokens(self, text):
        return len(text) // 4

    def prompt_tokens(self, system, prompt):
        return self.count_tokens(system + prompt)

    def generate_with_options(self, system, prompt, **kw):
        request, _ = json.JSONDecoder().raw_decode(prompt)
        if 'target_source' in request:
            return [json.dumps(self.manifest)]
        if 'protected_terms' in request:
            return [json.dumps({'question': request['question']})]
        if 'result_preview' in request:
            return [json.dumps({'checks': dict.fromkeys(('grain','joins','time','units','rules'), 'pass'),
                                'reason':'fixture check', 'required_tables':[], 'assertions':[]})]
        return [json.dumps({'type':'number','sql':"SELECT sum(thanh_tien) FROM sales WHERE trang_thai != 'huy'", 'reason':'sum eligible sales'})]


def dataset(tmp_path):
    root = tmp_path / 'data'
    write_split(root, n_questions=2)
    (root / 'calendar.csv').write_text('date,lunar_day,lunar_month,lunar_year,lunar_leap\n2026-08-27,15,7,2026,False\n')
    manifest = BusinessManifestService.build_draft(root / 'data/b1', (root / 'rulebook.md').read_text())
    return root, manifest


@pytest.mark.parametrize('parallel,profile', [
    (None, 'balanced'), ('1', 'balanced'), (None, 'rtx5090-6h'), (None, 'rtx5090-2gpu-6h')])
def test_worker_offline_incremental_and_deterministic(tmp_path, monkeypatch, parallel, profile):
    monkeypatch.setenv('DATAMATE_PROFILE', profile)
    monkeypatch.delenv('DATAMATE_TENSOR_PARALLEL', raising=False)
    if parallel:
        monkeypatch.setenv('DATAMATE_TENSOR_PARALLEL', parallel)
    root, manifest = dataset(tmp_path)
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if path.name == 'answers.jsonl':
            raise AssertionError('Worker must not read gold')
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('Network access'))
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    outputs = []
    for i in range(2):
        output = tmp_path / f'output{i}'
        backend = Transport(manifest)
        expected_parallel = int(parallel or ('1' if profile == 'rtx5090-6h' else '2'))
        def factory(cfg):
            assert cfg.tensor_parallel == expected_parallel
            return backend
        assert run(root, output, tmp_path/'model', backend_factory=factory, verify_weights=False) == 0
        assert json.loads((output/'logs/run.json').read_text())['config']['tensor_parallel'] == expected_parallel
        events = [json.loads(line) for line in (output/'logs/progress.jsonl').read_text().splitlines()]
        assert [(e['stage'], e['status']) for e in events if e['stage'] == 'question'] == [
            ('question', 'started'), ('question', 'completed')] * 2
        assert {'input', 'imports', 'model_verification', 'model_loading', 'shared_data',
                'business', 'manifest', 'ingestion', 'question', 'submission'} <= {e['stage'] for e in events}
        assert events[-1]['stage'] == 'submission' and events[-1]['completed_questions'] == 2
        assert [e['elapsed_s'] for e in events] == sorted(e['elapsed_s'] for e in events)
        if profile in ('rtx5090-6h', 'rtx5090-2gpu-6h'):
            assert all(e['model_calls'] == 5 for e in events if e['stage'] == 'question' and e['status'] == 'completed')
        if profile == 'rtx5090-2gpu-6h':
            assert len([e for e in events if e['stage'] == 'context' and e['status'] == 'completed']) == 1
        outputs.append((output/'submission.jsonl').read_bytes())
        assert validate_submission(str(output/'submission.jsonl'), ['q0','q1']) == []
        logs = [json.loads(p.read_text()) for p in sorted((output/'logs').glob('question_*.json'))]
        assert all(log['final_answer']['answer'] == 130000 and log['elapsed_s'] >= 0 for log in logs)
        assert all(log['sql'] and log['status']=='completed' for log in logs)
        assert all(type(log['elapsed_ms']) is int and log['elapsed_ms'] >= 0 for log in logs)
        assert all(log['answer'] == {'type':'number','answer':130000} and log['thinking'] is None for log in logs)
        assert all(log['code'] and log['output'] and log['model_calls'] for log in logs)
        assert all(log['elapsed_s'] >= log['preparation_s'] for log in logs)
        assert backend.seeds[-1] == seed_for(json.loads((root/'questions.jsonl').read_text().splitlines()[-1]))
    assert outputs[0] == outputs[1]
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes}


def test_single_gpu_preflight_never_accesses_second_device():
    from types import SimpleNamespace
    from docker.worker import gpu_preflight
    devices = []
    class Probe:
        def __matmul__(self, other): return self
        def float(self): return self
        def sum(self): return self
        def item(self): return 32768
    def device_name(index):
        assert index == 0
        return 'A100'
    def ones(shape, dtype, device):
        devices.append(device)
        return Probe()
    torch = SimpleNamespace(__version__='fixture', version=SimpleNamespace(cuda='12.8'),
        bfloat16='bf16', ones=ones, cuda=SimpleNamespace(is_available=lambda: True,
        device_count=lambda: 1, get_device_name=device_name,
        get_device_capability=lambda i: (8, 0),
        can_device_access_peer=lambda *a: pytest.fail('No second device')))
    assert gpu_preflight(torch, 1)['peer_access_0_1'] is None
    assert devices == ['cuda:0']
    with pytest.raises(RuntimeError, match='requires 2 CUDA'):
        gpu_preflight(torch, 2)


def test_preparation_timeout_does_not_skip_next_business(tmp_path, monkeypatch):
    import shutil
    from app.llm.base import InferenceBudgetExceeded
    from docker.runtime import ingest_before_deadline

    monkeypatch.setenv('DATAMATE_PROFILE', 'rtx5090-2gpu-6h')
    monkeypatch.delenv('DATAMATE_TENSOR_PARALLEL', raising=False)
    root, manifest = dataset(tmp_path)
    shutil.copytree(root/'data/b1', root/'data/b2')
    questions = [json.loads(line) for line in (root/'questions.jsonl').read_text().splitlines()]
    questions[1]['business_id'] = 'b2'
    (root/'questions.jsonl').write_text(''.join(json.dumps(q) + '\n' for q in questions))

    class MultiBusinessTransport(Transport):
        def generate_with_options(self, system, prompt, **kw):
            request, _ = json.JSONDecoder().raw_decode(prompt)
            replies = super().generate_with_options(system, prompt, **kw)
            if 'target_source' in request:
                reply = json.loads(replies[0])
                reply['business_id'] = request['business_id']
                return [json.dumps(reply)]
            return replies

    def ingest(business, *args):
        if business.name == 'b1':
            raise InferenceBudgetExceeded('Preparation budget exhausted during ingestion')
        return ingest_before_deadline(business, *args)

    monkeypatch.setattr('docker.worker.ingest_before_deadline', ingest)
    output = tmp_path/'out'
    assert run(root, output, tmp_path/'model', backend_factory=lambda cfg: MultiBusinessTransport(manifest),
               verify_weights=False) == 0
    rows = [json.loads(line) for line in (output/'submission.jsonl').read_text().splitlines()]
    assert rows == [{'id': 'q0', 'type': 'abstain'}, {'id': 'q1', 'type': 'number', 'answer': 130000}]
    report = json.loads((output/'logs/completion.json').read_text())
    assert report['completed_questions'] == 2 and report['answered_questions'] == 1
    assert report['preparation_budget_exhausted_businesses'] == report['preparation_failed_businesses'] == 1


def test_invalid_gpu_override_preserves_submission(tmp_path, monkeypatch):
    root, _ = dataset(tmp_path)
    monkeypatch.setenv('DATAMATE_TENSOR_PARALLEL', 'auto')
    with pytest.raises(ValueError, match='must be 1 or 2'):
        run(root, tmp_path/'out', tmp_path/'model',
            backend_factory=lambda cfg: pytest.fail('Invalid config must not load model'), verify_weights=False)
    assert validate_submission(str(tmp_path/'out/submission.jsonl'), ['q0','q1']) == []
    assert 'must be 1 or 2' in json.loads((tmp_path/'out/logs/fatal.json').read_text())['error']


def test_deadline_leaves_valid_pending_answers(tmp_path):
    root, _ = dataset(tmp_path)
    output = tmp_path/'out'
    assert run(root, output, tmp_path/'model', seconds=0,
               backend_factory=lambda cfg: pytest.fail('Model loaded after deadline'), verify_weights=False)==0
    assert all(json.loads(line)['type']=='abstain' for line in (output/'submission.jsonl').read_text().splitlines())
    assert len(list((output/'logs').glob('question_*.json'))) == 2


def test_journal_survives_later_failure(tmp_path):
    journal = OutputJournal([{'id':'a'}, {'id':'../b'}], tmp_path)
    journal.record({'id':'a','type':'number','answer':7}, {'elapsed_s':1.2})
    with pytest.raises(ValueError):
        journal.record({'id':'../b','type':'number','answer':True}, {'elapsed_s':0})
    rows = [json.loads(line) for line in (tmp_path/'submission.jsonl').read_text().splitlines()]
    assert rows == [{'id':'a','type':'number','answer':7}, {'id':'../b','type':'abstain'}]
    assert len(list((tmp_path/'logs').glob('question_*.json'))) == 2


def test_offline_missing_model_never_downloads(monkeypatch):
    from app.llm.local_common import resolve_model_path
    monkeypatch.setenv('HF_HUB_OFFLINE','1')
    with pytest.raises(FileNotFoundError, match='Offline'):
        resolve_model_path('missing/model')


def test_weight_inventory_and_tamper_detection(tmp_path):
    for name in ('config.json','tokenizer.json'):
        (tmp_path/name).write_text('{}')
    (tmp_path/'LICENSE').write_text('Apache-2.0')
    header = json.dumps({'weight':{'dtype':'BF16','shape':[2,3], 'data_offsets':[0,12]}}).encode()
    (tmp_path/'model.safetensors').write_bytes(struct.pack('<Q',len(header)) + header + bytes(12))
    report = inventory(tmp_path)
    assert report['parameters'] == 6
    report['revision_date'] = '2026-02-28T00:00:00+00:00'
    (tmp_path/'MODEL_MANIFEST.json').write_text(json.dumps(report))
    assert verify(tmp_path)['parameters']==6
    (tmp_path/'config.json').write_text('{"changed":true}')
    with pytest.raises(ValueError, match='changed'):
        verify(tmp_path)


def test_model_load_failure_preserves_baseline_and_logs(tmp_path):
    root, _ = dataset(tmp_path)
    def broken(cfg):
        raise RuntimeError('model load failed')
    with pytest.raises(RuntimeError, match='model load failed'):
        run(root, tmp_path/'out', tmp_path/'model', backend_factory=broken, verify_weights=False)
    assert validate_submission(str(tmp_path/'out/submission.jsonl'), ['q0','q1']) == []
    assert json.loads((tmp_path/'out/logs/fatal.json').read_text())['elapsed_s'] >= 0
    events = [json.loads(line) for line in (tmp_path/'out/logs/progress.jsonl').read_text().splitlines()]
    assert events[-1]['stage'] == 'pipeline' and events[-1]['status'] == 'fatal'


def test_vllm_adapter_budgets_and_seeds(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.config import Config
    from app.llm.vllm_local import VLLMBackend
    from app.llm.base import GenerationError
    samples = []
    class SamplingParams:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            samples.append(kwargs)
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(SamplingParams=SamplingParams))
    backend = object.__new__(VLLMBackend)
    backend.cfg = Config(local_model='Qwen/Qwen3.5-9B', max_model_len=100, max_new_tokens=20)
    backend.prompt_tokens = lambda *args: 50
    backend.llm = SimpleNamespace(chat=lambda *args, **kwargs: [SimpleNamespace(outputs=[SimpleNamespace(text='{}')])])
    backend.reset_seed(42)
    backend.generate_with_options('s','p',max_new_tokens=30,temperature=0)
    backend.generate('s','p')
    assert samples[0]['seed']==42 and samples[1]['seed']==43
    assert samples[0]['max_tokens']==30 and samples[0]['temperature']==0
    backend.reset_seed(42)
    backend.generate('s','p')
    assert samples[-1]['seed']==42
    with pytest.raises(GenerationError):
        backend.generate_with_options('s','p',max_new_tokens=51)


def test_audit_keeps_verbatim_reasoning_and_actual_executions(tmp_path):
    from types import SimpleNamespace
    from docker.audit import AuditedBackend, record_sql
    from app.tools.sql import SQLExecutor
    from docker.worker import atomic_json
    import duckdb
    raw = '<think>  first line\n' + 'x' * 5000 + '\nlast line  </think>{"type":"number"}'
    backend = AuditedBackend(SimpleNamespace(generate_with_options=lambda *a,**k:[raw]))
    assert backend.generate_with_options('system','question') == [raw]
    assert backend.calls[0]['thinking'] == [raw[len('<think>'):raw.index('</think>')]]
    original = SQLExecutor.query
    with duckdb.connect() as con:
        with record_sql() as calls:
            SQLExecutor.query(con, 'SELECT 7 AS answer')
            SQLExecutor.query(con, 'SELECT 1.0/0.0 AS nonfinite')
    assert SQLExecutor.query == original
    assert calls[0]['code'] == 'SELECT 7 AS answer' and calls[0]['output']['rows'] == [[7]]
    atomic_json(tmp_path/'log.json', calls)
    assert json.loads((tmp_path/'log.json').read_text())[1]['output']['rows'][0][0] == {'nonfinite_float':'inf'}


def test_training_manifest_contains_local_paths_sizes_and_checksums(tmp_path):
    from docker.build_artifacts import build
    train = tmp_path/'training_code/data/train';train.mkdir(parents=True)
    (train/'questions.jsonl').write_text('{"id":"q","business_id":"b"}\n')
    (train/'answers.jsonl').write_text('{"id":"q","type":"number","answer":1}\n')
    model = tmp_path/'models/Qwen3.5-9B';model.mkdir(parents=True)
    (model/'config.json').write_text('{}')
    manifest = {'files':{'config.json':hashlib.sha256(b'{}').hexdigest()}, 'repo':'Qwen/Qwen3.5-9B',
                'revision':'a'*40, 'revision_date':'2026-03-01T00:00:00+00:00',
                'original_release_date':'2026-02-28T00:00:00+00:00', 'licence':'apache-2.0', 'parameters':9}
    (model/'MODEL_MANIFEST.json').write_text(json.dumps(manifest))
    for name in ('README.md','predict.sh','requirements.txt'):
        (tmp_path/name).write_text('fixture')
    result = build(tmp_path)
    assert result['splits']['verification_questions']==1
    assert result['splits']['training_samples_used']==0
    assert result['splits']['profile'] == 'rtx5090-2gpu-6h'
    assert result['splits']['rewrite_questions'] and result['splits']['tensor_parallel'] == 2
    for entry in result['files']:
        path = Path(entry['path'])
        assert path.stat().st_size == entry['bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
    assert any(e['path'].endswith('/data/train/answers.jsonl') for e in result['files'])
    assert 'Original release date: 2026-02-28' in (tmp_path/'README.md').read_text()


def test_export_propagates_failed_docker_save(tmp_path, monkeypatch):
    import os
    import subprocess
    binary = tmp_path/'bin';binary.mkdir()
    docker = binary/'docker'
    docker.write_text('#!/usr/bin/env bash\nif [[ "$1" == "image" ]]; then echo linux/amd64; exit 0; fi\nprintf partial\nexit 42\n')
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary)+os.pathsep+os.environ['PATH'])
    output = tmp_path/'release'
    result = subprocess.run(['bash', 'docker/export.sh', 'fixture', str(output)], capture_output=True)
    assert result.returncode != 0
    assert not list(output.glob('*.tar.gz'))
    assert not list(output.glob('*.md5'))
    assert not list(output.glob('.export.*'))


def test_export_reports_actual_uncompressed_size_and_md5(tmp_path, monkeypatch):
    import gzip
    import os
    import subprocess
    binary = tmp_path/'bin';binary.mkdir()
    docker = binary/'docker'
    docker.write_text('#!/usr/bin/env bash\nif [[ "$1" == "image" && "$3" == "--format" ]]; then echo linux/amd64; exit 0; fi\nif [[ "$1" == "image" ]]; then echo "[]"; exit 0; fi\nprintf fixture_archive_bytes\n')
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary)+os.pathsep+os.environ['PATH'])
    output = tmp_path/'release'
    subprocess.run(['bash','docker/export.sh','fixture',str(output)], check=True, capture_output=True)
    archive = output/'zaic2026_datamate_fixture.tar.gz'
    assert gzip.decompress(archive.read_bytes()) == b'fixture_archive_bytes'
    assert int(Path(str(archive)+'.size.txt').read_text()) == len(b'fixture_archive_bytes')
    assert Path(str(archive)+'.md5').read_text() == hashlib.md5(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n'
