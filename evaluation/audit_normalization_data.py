"""Audit question normalization offline. Train is split by business; test is contract-only.

No answers are read, no model is called, and no dictionary is learned by this command.
Detailed question text and business records are never written to the report.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

from app.ingestion.dataset import load_tables
from app.ingestion.splitting import holdout_businesses
from app.normalization import QuestionNormalizer
from app.tools.grounding import ValueIndex
from app.text import norm


def read_questions(root: Path):
    return [json.loads(s) for s in (root / 'questions.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]


def contract(root: Path):
    questions = read_questions(root)
    keys = {'id', 'business_id', 'question', 'as_of_date'}
    return {'questions': len(questions), 'businesses': len({q.get('business_id') for q in questions}),
            'missing_fields': sum(not keys.issubset(q) for q in questions),
            'bad_question_types': sum(not isinstance(q.get('question'), str) for q in questions),
            'duplicate_ids': len(questions) - len({q.get('id') for q in questions}),
            'missing_business_dirs': sum(not (root / 'data' / str(q.get('business_id'))).is_dir() for q in questions),
            'extensions': dict(Counter(p.suffix.lower() for p in (root / 'data').rglob('*') if p.is_file()))}


def baseline_normalizer(repo: Path):
    spec = importlib.util.spec_from_file_location('baseline_app', repo / 'app/__init__.py',
                                                submodule_search_locations=[str(repo / 'app')])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from importlib import import_module
    return import_module('baseline_app.normalization').QuestionNormalizer()


def audit(train: Path, test: Path | None, *, baseline=None, with_values=False, holdout_n=6):
    questions = read_questions(train)
    held = set(holdout_businesses([q['business_id'] for q in questions], holdout_n))
    normalizers = {'current': QuestionNormalizer()}
    if baseline:
        normalizers['baseline'] = baseline_normalizer(baseline)
    summary = {group: {name: {'questions': 0, 'changed': 0, 'errors': 0, 'unresolved_questions': 0,
                             'corrections': 0, 'harmful_accent_collisions': 0, 'literal_changes': 0}
                      for name in normalizers} for group in ['fit', 'holdout']}
    failures = []
    started = time.monotonic()
    for biz_id in sorted({q['business_id'] for q in questions}):
        group = 'holdout' if biz_id in held else 'fit'
        schema = readme = ''
        values, index = (), None
        value_buckets = {}
        if with_values:
            print(f'{group}: loading {biz_id}', flush=True)
            directory = train / 'data' / biz_id
            tables = load_tables(str(directory))
            if not tables:
                raise ValueError(f'No tables loaded for {biz_id}')
            readme_path = directory / 'README.md'
            readme = readme_path.read_text(encoding='utf-8') if readme_path.exists() else ''
            schema = ' '.join(str(k) + ' ' + ' '.join(map(str, df.columns)) for k, df in tables.items())
            index = ValueIndex(tables)
            values = index.by_norm
            for value in values:
                words = re.findall(r'\w+', unicodedata.normalize('NFC', value))
                if words:
                    value_buckets.setdefault(norm(words[0]), []).append(value)
        for row in (q for q in questions if q['business_id'] == biz_id):
            raw = row['question']
            # Exact protection can only match a value starting with a token present in
            # the question. Filtering by this necessary condition avoids rebuilding
            # tens of thousands of irrelevant value keys for every question.
            query_words = {norm(w) for w in re.findall(r'\w+', unicodedata.normalize('NFC', raw))}
            relevant_values = [v for word in query_words for v in value_buckets.get(word, ())] if index else ()
            literals = re.findall(r'\b[\w./:+%-]*\d[\w./:+%-]*\b', raw)
            for name, normalizer in normalizers.items():
                stats = summary[group][name]
                stats['questions'] += 1
                try:
                    result = normalizer.normalize(raw, schema=schema, readme=readme,
                                                  protected_values=relevant_values)
                    stats['changed'] += result.normalized_question != raw
                    stats['unresolved_questions'] += result.has_ambiguity
                    stats['corrections'] += len(result.corrections)
                    stats['harmful_accent_collisions'] += sum(
                        c['original'].casefold() in {'nhiêu', 'họng'} and c['replacement'] != c['original']
                        for c in result.corrections)
                    stats['literal_changes'] += sum(l not in result.normalized_question for l in literals)
                except Exception as exc:
                    stats['errors'] += 1
                    failures.append({'group': group, 'normalizer': name, 'error_type': type(exc).__name__})
        print(f'{group}: {biz_id} complete ({time.monotonic()-started:.1f}s)', flush=True)
    return {'method': 'runtime and preservation audit, NOT labeled normalization accuracy or answer PA',
            'train_contract': contract(train), 'test_contract': contract(test) if test else None,
            'test_usage': 'format/IDs/business-directory contract only; no test vocabulary, corrections or answers used',
            'dictionary_source': 'generic lexicon plus curated train-fit variants only',
            'holdout_business_count': len(held), 'with_train_schema_and_values': with_values,
            'entity_protection': 'all exact ValueIndex matches; fuzzy grounding is covered separately by integration tests',
            'summary': summary, 'failures': failures, 'elapsed_s': round(time.monotonic()-started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train-dir', type=Path, required=True)
    parser.add_argument('--test-dir', type=Path)
    parser.add_argument('--baseline-repo', type=Path)
    parser.add_argument('--with-train-values', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.train_dir, args.test_dir, baseline=args.baseline_repo, with_values=args.with_train_values)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report['failures']))


if __name__ == '__main__':
    raise SystemExit(main())
