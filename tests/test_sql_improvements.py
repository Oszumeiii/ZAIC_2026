import json
from types import SimpleNamespace
import duckdb
import pandas as pd
import pytest
from app.agent.sql_review import verify, DIMENSIONS
from app.submission import validate_answer
from evaluation.confidence import confidence_report
from training.sql_examples import export_sql_examples
from app.llm.local_service import LocalLLMService
from app.config import Config
from app.llm.base import GenerationError


def test_review_executes_assertions_and_rejects_fanout():
    report = {'checks': dict.fromkeys(DIMENSIONS, 'pass'), 'reason': 'check grain',
              'required_tables': [], 'assertions': [{'sql': 'SELECT count(*) = count(DISTINCT id) FROM parent', 'reason': 'parent key unique'}]}
    service = SimpleNamespace(json=lambda *a, **k: report, cfg=Config())
    with duckdb.connect() as con:
        con.execute('CREATE TABLE parent AS SELECT 1 id UNION ALL SELECT 1')
        with pytest.raises(ValueError, match='assertion failed'):
            verify(service, con, {}, {'sql': 'SELECT sum(id) FROM parent'}, ['s'], [(2,)])
        con.execute('DELETE FROM parent WHERE rowid=1')
        assert verify(service, con, {}, {'sql': 'SELECT sum(id) FROM parent'}, ['s'], [(1,)]) == report
        report['checks']['units'] = 'unknown'
        with pytest.raises(ValueError, match='review failed'):
            verify(service, con, {}, {'sql': ''}, [], [])


@pytest.mark.parametrize('requested', ['sales', 'calendar', 'semantic.sales_summary'])
def test_review_already_visible_tables_do_not_skip_checks(requested):
    report = {'checks': dict.fromkeys(DIMENSIONS, 'pass'), 'reason': 'verified',
              'required_tables': [requested], 'assertions': []}
    context = {'tables': [{'name': 'sales'}], 'calendar': {'name': 'calendar'},
               'semantic_views': [{'schema': 'semantic', 'name': 'sales_summary'}]}
    service = SimpleNamespace(json=lambda *a, **k: report, cfg=Config())
    with duckdb.connect() as con:
        result = verify(service, con, context, {'sql': 'SELECT 1'}, ['n'], [[1]])
        assert result['required_tables'] == []
        assert report['required_tables'] == [requested]
        report['checks']['rules'] = 'unknown'
        with pytest.raises(ValueError, match='Business review failed'):
            verify(service, con, context, {'sql': 'SELECT 1'}, ['n'], [[1]])
        report['checks']['rules'] = 'pass'
        report['assertions'] = [{'sql': 'SELECT FALSE', 'reason': 'bad grain'}]
        with pytest.raises(ValueError, match='Business assertion failed'):
            verify(service, con, context, {'sql': 'SELECT 1'}, ['n'], [[1]])
        report['assertions'] = []
        with pytest.raises(ValueError, match='requires grain/cardinality assertions'):
            verify(service, con, context,
                   {'sql': 'SELECT SUM(a.id) FROM sales a JOIN sales b USING (id)'}, ['n'], [[1]])


def test_review_requests_only_omitted_tables_and_rejects_unknown_names():
    report = {'checks': dict.fromkeys(DIMENSIONS, 'unknown'), 'reason': 'need schema',
              'required_tables': ['sales', 'products', 'products'], 'assertions': []}
    context = {'tables': [{'name': 'sales'}], 'omitted_tables': ['products']}
    service = SimpleNamespace(json=lambda *a, **k: report, cfg=Config())
    result = verify(service, None, context, {'sql': 'SELECT 1'}, ['n'], [[1]])
    assert result['required_tables'] == ['products']
    report['required_tables'] = ['invented']
    with pytest.raises(ValueError, match='Unknown required tables'):
        verify(service, None, context, {'sql': 'SELECT 1'}, ['n'], [[1]])


@pytest.mark.parametrize('fits', [True, False])
def test_context_rejects_unknown_required_tables_before_budget_shortcuts(fits):
    service = LocalLLMService(None, Config())
    service.fits = lambda *args: fits
    context = {'tables': [{'name': 'sales', 'columns': [], 'sample_rows': []}],
               'original_question': 'sales'}
    with pytest.raises(GenerationError, match='Unknown required tables'):
        service.fit_sql_context(context, '', {}, required_tables=['invented'])


@pytest.mark.parametrize('typ,answer', [('boolean', 1), ('number', True), ('percent', '12%'), ('date','2026-02-30'), ('table', {'columns':['a'], 'rows':[[1,2]]})])
def test_strict_format(typ, answer):
    with pytest.raises(ValueError):
        validate_answer({'id':'q', 'type':typ, 'answer':answer})


def test_precision_lower_bound_and_vote_buckets():
    ev = pd.DataFrame([{'id':str(i),'type':'number','status':'correct' if i<9 else 'wrong'} for i in range(10)])
    report = confidence_report(ev, [{'id':str(i), 'agree':2} for i in range(10)], 'local')
    assert report['buckets']['number:2']['precision'] == .9
    assert report['buckets']['number:2']['lower_precision'] < .9


def test_context_preserves_bridge_component_and_restores_requested_table():
    # A controlled token budget ensures only two schemas fit.
    service = LocalLLMService(None, Config())
    service.fits = lambda system,prompt,budget: len(json.loads(prompt)['tables']) <= 2
    tables = [{'name':name, 'columns':[], 'sample_rows':[]} for name in ('sales','payments','other')]
    context = {'tables': tables, 'original_question':'sales',
               'relationships':[{'from_table':'sales','to_table':'payments'}]}
    fitted = service.fit_sql_context(context, '', {})
    assert {t['name'] for t in fitted['tables']} == {'sales','payments'}
    fitted = service.fit_sql_context(context, '', {}, required_tables=['other'])
    assert 'other' in {t['name'] for t in fitted['tables']}
    with pytest.raises(GenerationError):
        service.fit_sql_context(context, '', {}, required_tables=['sales','other'])
    assert len(context['tables']) == 3


def test_export_rejects_holdout(tmp_path):
    split = SimpleNamespace(questions=pd.DataFrame([{'id':'q','business_id':'b','question':'sum?'}]))
    ev = pd.DataFrame([{'id':'q','status':'correct'}])
    with pytest.raises(ValueError, match='held-out'):
        export_sql_examples(ev, [{'id':'q','sql':'SELECT 1', 'type':'number'}], split, tmp_path/'bank', holdout_n=1)
    assert not (tmp_path/'bank').exists()


def test_export_sql_contains_context_and_no_gold(tmp_path):
    from app.ingestion.datasets.splitting import BusinessSplitter
    questions = pd.DataFrame([{'id':str(i), 'business_id':f'b{i}', 'question':'sum?'} for i in range(4)])
    held = set(BusinessSplitter.holdout_businesses(questions.business_id, 1))
    q = next(q for q in questions.itertuples() if q.business_id not in held)
    split = SimpleNamespace(questions=questions)
    ev = pd.DataFrame([{'id':q.id, 'status':'correct'}])
    log = {'id':q.id, 'type':'number', 'sql':'SELECT SUM(amount) FROM sales',
           'training_context':'schema + question', 'note':'sum sales'}
    assert export_sql_examples(ev, [log], split, tmp_path/'bank', holdout_n=1, sft_path=tmp_path/'sft') == 1
    assert json.loads((tmp_path/'bank').read_text())['query_engine'] == 'sql'
    assert json.loads((tmp_path/'sft').read_text())['messages'][1]['content'] == 'schema + question'


def test_calibration_invalidates_on_policy_changes(tmp_path):
    from evaluation.confidence import policy_signature
    from app.agent.sql_solver import SQLSolver
    backend = SimpleNamespace(tag=lambda: 'local')
    cfg = Config()
    report = {'model':'local', 'policy_signature':policy_signature(cfg, backend), 'buckets':{}}
    path = tmp_path/'confidence.json';path.write_text(json.dumps(report))
    cfg.confidence_path = str(path)
    SQLSolver(cfg, backend)
    cfg.agree_min = 3
    with pytest.raises(ValueError, match='different model'):
        SQLSolver(cfg, backend)


def test_month_date_answer_preserves_gold_format():
    from app.tools.sql import SQLExecutor
    assert SQLExecutor.answer('date', ['month'], [['2025-12']]) == '2025-12'
    validate_answer({'id':'q', 'type':'date', 'answer':'2025-12'})
    for invalid in ('2025-13', '2025-00', '2025-02-30'):
        with pytest.raises(ValueError):
            SQLExecutor.answer('date', ['date'], [[invalid]])
        with pytest.raises(ValueError):
            validate_answer({'id':'q', 'type':'date', 'answer':invalid})
