"""Estimate full-run time from progress events without counting shared logs twice."""
import argparse
import json
from pathlib import Path
from statistics import mean


def summarize(output):
    output = Path(output)
    events = []
    lines = (output / 'logs/progress.jsonl').read_text().splitlines()
    for i, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1:
                raise
            # A live writer may not have finished its final line yet.
    run_path = output / 'logs/run.json'
    run = json.loads(run_path.read_text()) if run_path.exists() else {}
    shared = next((e for e in events if e['stage'] == 'shared_data' and e['status'] == 'completed'), {})
    done = [e for e in events if e['stage'] == 'question' and e['status'] == 'completed']
    durations = [e['duration_s'] for e in done if 'duration_s' in e]
    preparation = {}
    complete_preparation = {}
    for e in events:
        if e['stage'] in ('manifest', 'ingestion') and e['status'] == 'completed' and 'duration_s' in e:
            preparation.setdefault(e['business'], {})[e['stage']] = e['duration_s']
        if (e['stage'] == 'preparation' and e['status'] == 'completed') or e['status'] == 'preparation_failed':
            if 'duration_s' in e:
                complete_preparation[e['business']] = e['duration_s']
    prep_by_business = {business: sum(stages.values()) for business, stages in preparation.items()
                        if len(stages) == 2}
    prep_by_business.update(complete_preparation)  # include context once, never add it twice
    prep = list(prep_by_business.values())
    total, businesses = shared.get('questions'), shared.get('businesses')
    startup = shared.get('elapsed_s', 0)
    budget = run.get('time_budget_s', 21300)
    estimated_preparation = mean(prep) * businesses if prep and businesses else None
    projected = (startup + estimated_preparation + mean(durations) * total
                 if durations and estimated_preparation is not None and total else None)
    return {
        'profile': run.get('profile'), 'total_questions': total,
        'attempted_questions': len(done),
        'answered_questions': sum(e['answer_type'] != 'abstain' for e in done),
        'budget_exhausted_questions': sum(e.get('budget_exhausted', False) for e in done),
        'preparation_failed_businesses': sum(e['status'] == 'preparation_failed' for e in events),
        'mean_question_s': mean(durations) if durations else None,
        'mean_preparation_s': mean(prep) if prep else None,
        'planned_target_mean_question_s': (
            max(0, (budget - startup - run['preparation_budget_s']) / total)
            if total and run.get('preparation_budget_s') is not None else None),
        'target_mean_question_s': (max(0, (budget - startup - estimated_preparation) / total)
                                   if total and estimated_preparation is not None else None),
        'projected_total_s': projected,
        'projected_within_budget': projected <= budget if projected is not None else None,
        'note': 'Projection from observed businesses/questions, not a benchmark guarantee. '
                'Check answered and budget-exhausted counts; fast abstentions can hide low coverage.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.output), ensure_ascii=False, indent=2))
