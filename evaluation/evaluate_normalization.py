"""Reproducible linguistic evaluation on curated synthetic queries; never calls a model.

Incorrect replacement rate is case-level: changed outputs differing from the gold text /
all changed outputs. It is not a per-token error estimate or downstream answer accuracy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.normalization import QuestionNormalizer


def evaluate(path: Path | None = None) -> dict:
    path = path or Path(__file__).with_name('normalization_cases.json')
    cases = json.loads(path.read_text(encoding='utf-8'))
    normalizer = QuestionNormalizer()
    exact = changed = bad_changes = ambiguous = preserved = entities = kept = 0
    failures = []
    for i, case in enumerate(cases):
        raw = case['question']
        result = normalizer.normalize(raw, schema=case.get('schema', ''), readme=case.get('readme', ''),
                                      protected_values=case.get('values', []))
        output = result.normalized_question
        exact += output == case['expected']
        changed += output != raw
        bad_changes += output != raw and output != case['expected']
        unresolved = {t['original'] for t in result.unresolved_terms}
        for term in case.get('ambiguous', []):
            ambiguous += 1
            preserved += term in unresolved and term in output
        for entity in case.get('entities', []):
            entities += 1
            kept += entity in output
        if output != case['expected']:
            failures.append({'case': i, 'question': raw, 'expected': case['expected'], 'actual': output})
    return {'cases': len(cases), 'exact_correct': exact, 'exact_accuracy': exact / max(1, len(cases)),
            'changed_cases': changed, 'incorrect_replacement_cases': bad_changes,
            'incorrect_replacement_rate': bad_changes / max(1, changed),
            'ambiguous_terms': ambiguous, 'ambiguous_terms_preserved': preserved,
            'ambiguous_preservation_rate': preserved / max(1, ambiguous),
            'entities': entities, 'entities_preserved': kept, 'entity_preservation_rate': kept / max(1, entities),
            'dictionary_entries': normalizer.lexicon.counts, 'failures': failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = evaluate(args.cases)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')
    return int(bool(report['failures']) or report['ambiguous_preservation_rate'] < 1 or report['entity_preservation_rate'] < 1)


if __name__ == '__main__':
    raise SystemExit(main())
