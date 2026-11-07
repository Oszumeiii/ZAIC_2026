"""Linguistic correctness and false-replacement guards; synthetic, no business/test data."""
import json
import unittest
import unicodedata
from pathlib import Path

from app.normalization import QuestionNormalizer
from app.normalization.fuzzy_matcher import FuzzyMatcher


class NormalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = QuestionNormalizer()

    def test_required_positive_cases(self):
        cases = {
            "sp nào bán nhiều nhất": "sản phẩm nào bán nhiều nhất",
            "sl hàng tồn kho": "số lượng hàng tồn kho",
            "doanh thuu tháng này": "doanh thu tháng này",
            "sp nào bán nhìu nhất": "sản phẩm nào bán nhiều nhất",
            "san pham nao ban chay nhat": "sản phẩm nào bán chạy nhất",
            "sp nào bán nhìu nhất thg trc": "sản phẩm nào bán nhiều nhất tháng trước",
            "doanh thuu thág này bn": "doanh thu tháng này bao nhiêu",
            "hqa bán dc bnhieu": "hôm qua bán được bao nhiêu",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                r = self.n.normalize(raw)
                self.assertEqual(r.original_question, raw)
                self.assertEqual(r.normalized_question, expected)

    def test_unicode_and_punctuation(self):
        raw = unicodedata.normalize('NFD', '  sản phẩm\u200b  nào\u00a0bán nhiều nhất？ ')
        r = self.n.normalize(raw)
        self.assertEqual(r.normalized_question, 'sản phẩm nào bán nhiều nhất?')
        self.assertEqual(r.original_question, raw)
        self.assertEqual(r.corrections[0]['method'], 'unicode_whitespace')

    def test_ambiguity_without_evidence(self):
        for raw, term in [('dt tháng này', 'dt'), ('dt khách hàng', 'dt'),
                          ('t7 bán được bao nhiêu', 't7'), ('ck tháng này', 'ck'),
                          ('tk tháng này', 'tk'), ('cp tháng này', 'cp'), ('ln tháng này', 'ln')]:
            with self.subTest(raw=raw):
                r = self.n.normalize(raw)
                self.assertEqual(r.normalized_question, raw)
                self.assertTrue(r.has_ambiguity)
                self.assertIn(term, [u['original'] for u in r.unresolved_terms])
                self.assertGreaterEqual(len(r.unresolved_terms[0]['candidates']), 2)

    def test_context_requires_local_and_domain_evidence(self):
        r = self.n.normalize('dt tháng này bao nhiêu', schema='sales doanh_thu thanh_tien')
        self.assertEqual(r.normalized_question, 'doanh thu tháng này bao nhiêu')
        self.assertEqual(r.corrections[0]['method'], 'context_rule')
        self.assertEqual(self.n.normalize('dt', schema='sales doanh_thu').normalized_question, 'dt')
        self.assertEqual(self.n.normalize('sdt của kh A', schema='khach_hang customer phone').normalized_question,
                         'số điện thoại của khách hàng A')

    def test_conflicting_context_does_not_guess(self):
        r = self.n.normalize('dt khách hàng tháng này', schema='khach_hang phone doanh_thu')
        self.assertIn('dt', r.normalized_question)
        self.assertTrue(r.has_ambiguity)

    def test_entities_and_identifiers(self):
        for q in ['SP-001 bán bao nhiêu', 'KH001 còn nợ không', 'Trà sữa 2T có tồn kho không',
                  '100k và 1.250.000đ ngày 01/07/2026', 'mã SP bán bao nhiêu',
                  'SELECT sp, sl FROM sales WHERE kh = 1', 'SUM(sl) > 10', 'dt = 100',
                  'cột `sp` = "doanh thuu"', '0901 234 567', 'https://shop.vn/sp/sl']:
            with self.subTest(q=q):
                self.assertEqual(self.n.normalize(q).normalized_question, q)

    def test_known_and_grounded_names_protected(self):
        r = self.n.normalize('sp của KH A và Ngô thơm', protected_values=['KH A', 'Ngô thơm'])
        self.assertEqual(r.normalized_question, 'sản phẩm của KH A và Ngô thơm')
        self.assertEqual(self.n.normalize('doanh thuu', protected_values=['doanh thuu']).normalized_question,
                         'doanh thuu')
        self.assertEqual(self.n.normalize('tra sua tran chao', grounded_phrases=['tra sua tran chao']).normalized_question,
                         'tra sua tran chao')
        self.assertEqual(self.n.normalize('sp của SP - Coffee', protected_values=['SP - Coffee']).normalized_question,
                         'sản phẩm của SP - Coffee')
        self.assertEqual(self.n.normalize('hàng “SP  A” có ko').normalized_question, 'hàng “SP  A” có không')
        sql = 'UPDATE sales SET sp = sl'
        self.assertEqual(self.n.normalize(sql).normalized_question, sql)
        self.assertEqual(self.n.normalize('sl * don_gia').normalized_question, 'sl * don_gia')

    def test_no_substring_or_recursive_replacement(self):
        self.assertEqual(self.n.normalize('special sp001 sl_code koala').normalized_question,
                         'special sp001 sl_code koala')
        r = self.n.normalize('sp nào bán nhìu nhất thg trc')
        self.assertEqual(self.n.normalize(r.normalized_question).normalized_question, r.normalized_question)
        self.assertEqual(sum(c['original'] == 'thg trc' for c in r.corrections), 1)

    def test_case_and_quotes_preserved(self):
        q = 'Sản phẩm "SP  A" bán nhiều nhất?'
        self.assertEqual(self.n.normalize(q).normalized_question, q)

    def test_short_and_unknown_fuzzy_words_are_not_rewritten(self):
        for q in ['an', 'lan', 'mai', 'Tổng của Lan', 'cf', 'Công ty Phúc An']:
            self.assertEqual(self.n.normalize(q, schema='doanh thu chi phi khach hang').normalized_question, q)
        r = self.n.normalize('doanh thux tháng này')
        self.assertEqual(r.normalized_question, 'doanh thux tháng này')

    def test_fuzzy_requires_domain_and_linguistic_anchor(self):
        r = self.n.normalize('doanh thux tháng này', schema='sales doanh_thu thanh_tien')
        self.assertEqual(r.normalized_question, 'doanh thu tháng này')
        self.assertIn('fuzzy_context', [c['method'] for c in r.corrections])
        candidates = self.n.fuzzy.candidates('doanh thux')
        self.assertGreater(len(candidates), 0)
        self.assertIn('score', candidates[0])
        self.assertEqual(self.n.fuzzy.candidates('SP001'), [])
        uncertain = self.n.normalize('gia bon')
        self.assertEqual(uncertain.normalized_question, 'gia bon')
        self.assertGreaterEqual(len(uncertain.unresolved_terms[0]['candidates']), 2)
        with self.assertRaises(ValueError):
            FuzzyMatcher(self.n.lexicon, word_threshold=2)

    def test_money_k_not_negated(self):
        r = self.n.normalize('có 100 k trong tk', schema='san_pham tai_khoan so_du')
        self.assertIn('100 k', r.normalized_question)

    def test_metadata_is_json_and_hints_bounded(self):
        r = self.n.normalize('dt tháng này')
        self.assertEqual(json.loads(json.dumps(r.to_dict()))['original_question'], 'dt tháng này')
        self.assertIn('chưa kết luận', r.prompt_section())
        self.assertNotIn('GỢI Ý CHUẨN HÓA', self.n.normalize('sp').prompt_section(False))

    def test_reproducible_evaluation_suite(self):
        from evaluation.evaluate_normalization import evaluate
        report = evaluate()
        self.assertEqual(report['exact_accuracy'], 1)
        self.assertEqual(report['incorrect_replacement_rate'], 0)
        self.assertEqual(report['ambiguous_preservation_rate'], 1)
        self.assertEqual(report['entity_preservation_rate'], 1)

    def test_distinct_accented_words_are_not_aliases(self):
        for raw in ['nhiêu', 'họng', 'đau họng có hàng không', 'thuốc nhóm mắt mũi họng',
                    'tỉ lệ là nhiêu %', 'Số tiền là bao nhiêu?']:
            with self.subTest(raw=raw):
                self.assertEqual(self.n.normalize(raw).normalized_question, raw)

    def test_single_ascii_canonical_keys_do_not_invent_accents(self):
        for raw in ['vay', 'chua', 'voi', 'roi', 'so', 'la', 'cua']:
            self.assertEqual(self.n.normalize(raw).normalized_question, raw)
        # Mixed accented/unaccented phrases covered by dictionaries still work.
        self.assertEqual(self.n.normalize('san phẩm nào').normalized_question, 'sản phẩm nào')

    def test_observed_telex_and_abbreviations(self):
        cases = {
            'dthu hqua bn': 'doanh thu hôm qua bao nhiêu',
            'thangs này có bao nhieeu tieenf': 'tháng này có bao nhiêu tiền',
            'cuwar hangf bán dduwowcj bn': 'cửa hàng bán được bao nhiêu',
            'sp bán dc nhieeuf nhất': 'sản phẩm bán được nhiều nhất',
            'soos đơn laf bn': 'số đơn là bao nhiêu',
            'so cùng kỳ': 'so cùng kỳ',
            'khachs còn nowj': 'khách còn nợ',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.n.normalize(raw).normalized_question, expected)

    def test_brand_names_are_not_generically_telex_decoded(self):
        raw = 'Namilux Powerflexx Plusssz gas'
        self.assertEqual(self.n.normalize(raw).normalized_question, raw)

    def test_train_fit_curated_variants_have_reviewed_forms(self):
        path = Path(__file__).resolve().parents[2] / 'docs/normalization_train_evidence.json'
        evidence = json.loads(path.read_text(encoding='utf-8'))
        for item in evidence['entries']:
            with self.subTest(variant=item['variant']):
                self.assertEqual(item['source'], 'train_fit_only')
                self.assertGreater(item['fit_occurrences'], 0)
                self.assertEqual(self.n.normalize(item['variant']).normalized_question, item['canonical'])


if __name__ == '__main__':
    unittest.main()
