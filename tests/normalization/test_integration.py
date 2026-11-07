"""Real ContextBuilder, grounding, CLI, cache and solver contracts with fake data/backend."""
import argparse
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import json
from unittest.mock import patch

from app.agent.context import ContextBuilder
from app.agent.prompts import HELPERS_DOC, SYSTEM, build_system, prompt_hash
from app.agent.solver import Solver
from app.config import Config
from app.ingestion.dataset import load_split
from app.main import add_common_args, config_from_args
from evaluation.fakes import FakeBackend, GOOD_CODE, write_split


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        write_split(root / 'train')
        self.cfg = Config(backend='gemini', train_dir=str(root / 'train'), out_dir=str(root / 'out'))
        self.split = load_split(self.cfg.train_dir)
        self.biz = self.split.business('b1')

    def build(self, q, cfg=None):
        return ContextBuilder(cfg or self.cfg).build(q, '2026-08-27', self.biz, self.split, 'b1')[0]

    def test_original_normalized_and_existing_grounding_preserved(self):
        raw = 'sp ca phe bán nhìu nhất thg trc'
        ctx = self.build(raw)
        self.assertIn('CÂU HỎI GỐC:\n' + raw, ctx)
        self.assertIn('sản phẩm ca phe bán nhiều nhất tháng trước', ctx)
        self.assertIn("khớp giá trị 'Ca phe'", ctx)
        self.assertIn('2026-07-01 đến 2026-07-31', ctx)
        self.assertIn('GỢI Ý CHUẨN HÓA', ctx)

    def test_ambiguous_prompt_warns_and_does_not_choose(self):
        ctx = self.build('t7 bán được bao nhiêu')
        self.assertIn('TỪ/CỤM TỪ CHƯA XÁC ĐỊNH', ctx)
        self.assertIn('tháng 7 / thứ Bảy', ctx)
        self.assertIn('nguồn chính', build_system(self.cfg))

    def test_no_hints_preserves_normalized_question_and_ambiguity(self):
        ctx = self.build('sp thg trc', replace(self.cfg, use_hints=False))
        self.assertNotIn('GỢI Ý', ctx)
        self.assertIn('sản phẩm tháng trước', ctx)
        self.assertIn('TỪ/CỤM TỪ CHƯA XÁC ĐỊNH', self.build('dt', replace(self.cfg, use_hints=False)))

    def test_disabled_exact_old_context_and_system(self):
        cfg = replace(self.cfg, enable_question_normalization=False)
        builder = ContextBuilder(cfg)
        q = 'sp ca phe thg trc'
        ctx, tables = builder.build(q, '2026-08-27', self.biz, self.split, 'b1')
        extra = builder._extra(q, '2026-08-27', self.biz, tables, 'b1')
        old = builder._build(q, '2026-08-27', self.biz, self.split.rulebook, self.biz.cache['profiles'][0],
                             builder.rulebook_limit, builder.readme_limit, extra)
        self.assertEqual(ctx, old)
        self.assertEqual(build_system(cfg), SYSTEM + HELPERS_DOC)
        self.assertNotIn('CÂU HỎI ĐÃ CHUẨN HÓA', ctx)
        self.assertIn('CÂU HỎI: ' + q, ctx)

    def test_toggle_invalidates_prompt_cache(self):
        a = prompt_hash(self.cfg, 'fake', 'ctx')
        self.assertNotEqual(a, prompt_hash(replace(self.cfg, enable_question_normalization=False), 'fake', 'ctx'))

    def test_fuzzy_entity_grounding_retained(self):
        import pandas as pd
        self.biz.tables['items'] = pd.DataFrame({'name': ['Trà sữa trân châu']})
        self.biz.cache.clear()
        ctx = self.build('sp tra sua tran chao thg trc')
        self.assertIn('Trà sữa trân châu', ctx)
        self.assertIn('khớp gần đúng', ctx)
        self.assertIn('tra sua tran chao', ctx)

    def test_cli_switch(self):
        p = add_common_args(argparse.ArgumentParser())
        self.assertTrue(config_from_args(p.parse_args([])).enable_question_normalization)
        self.assertFalse(config_from_args(p.parse_args(['--no-question-normalization'])).enable_question_normalization)

    def test_no_new_llm_call_and_submission_unchanged(self):
        calls = []
        for enabled in (False, True):
            cfg = replace(self.cfg, enable_question_normalization=enabled, out_dir=str(Path(self.tmp.name) / str(enabled)))
            backend = FakeBackend([GOOD_CODE])
            # This test checks integration/call counts, not the existing Unix-only sandbox.
            with patch('app.agent.solver.run_code', return_value=('number', 130000)):
                subs, _ = Solver(cfg, backend).solve_split(self.split, 'train', limit_q=1)
            self.assertEqual(subs, [{'id': 'q0', 'type': 'number', 'answer': 130000}])
            calls.append(backend.calls)
        self.assertEqual(calls[0], calls[1])

    def test_token_budget_fallback_retains_questions(self):
        cfg = replace(self.cfg, backend='local', max_model_len=2300, max_new_tokens=100)
        ctx, _ = ContextBuilder(cfg, count_tokens=lambda s: len(s)//3).build(
            'sp thg trc', '2026-08-27', self.biz, self.split)
        self.assertIn('CÂU HỎI GỐC:\nsp thg trc', ctx)
        self.assertIn('sản phẩm tháng trước', ctx)
        self.assertLessEqual(len(ctx)//3, cfg.token_budget)

    def test_real_data_audit_never_normalizes_test_vocabulary(self):
        from evaluation.audit_normalization_data import audit
        from app.normalization import QuestionNormalizer
        test_root = Path(self.tmp.name) / 'test_contract'
        write_split(test_root, n_questions=1, with_answers=False)
        qpath = test_root / 'questions.jsonl'
        row = json.loads(qpath.read_text(encoding='utf-8').splitlines()[0])
        row['question'] = 'TEST_ONLY_VOCABULARY'
        qpath.write_text(json.dumps(row) + '\n', encoding='utf-8')
        normalizer = QuestionNormalizer()
        seen = []
        original = normalizer.normalize

        def capture(question, **kwargs):
            seen.append(question)
            return original(question, **kwargs)

        with patch('evaluation.audit_normalization_data.QuestionNormalizer', return_value=normalizer), \
                patch.object(normalizer, 'normalize', side_effect=capture):
            report = audit(Path(self.cfg.train_dir), test_root, holdout_n=1)
        self.assertEqual(len(seen), 4)
        self.assertNotIn('TEST_ONLY_VOCABULARY', seen)
        self.assertEqual(report['test_contract']['questions'], 1)

    def test_train_evidence_is_separate_from_synthetic_accuracy(self):
        from evaluation.audit_normalization_data import contract
        report = contract(Path(self.cfg.train_dir))
        self.assertEqual(report['questions'], 4)
        self.assertEqual(report['missing_fields'], 0)
        self.assertNotIn('question_text', report)


if __name__ == '__main__':
    unittest.main()
