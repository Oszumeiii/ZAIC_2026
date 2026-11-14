"""Export scored SQL solutions from fit businesses only, with schema context for SFT."""
import json
from pathlib import Path
from app.ingestion.datasets.splitting import BusinessSplitter
from app.agent.sql_solver import SQL_SYSTEM


def export_sql_examples(ev, logs, split, path, *, holdout_n=6, sft_path=None):
    held = set(BusinessSplitter.holdout_businesses(split.questions.business_id, holdout_n))
    questions = split.questions.set_index('id')
    correct = set(ev.loc[ev.status == 'correct', 'id'])
    items, sft = [], []
    for log in logs:
        if log['id'] not in correct or not log.get('sql'):
            continue
        question = questions.loc[log['id']]
        if question.business_id in held:
            raise ValueError('Cannot export held-out business into SQL examples')
        item = {k: log[k] for k in ('id', 'type', 'sql')}
        item.update(question=question.question, business_id=question.business_id, query_engine='sql')
        items.append(item)
        if log.get('training_context'):
            sft.append({'messages': [{'role': 'system', 'content': SQL_SYSTEM},
                {'role': 'user', 'content': log['training_context']},
                {'role': 'assistant', 'content': json.dumps({'type': log['type'], 'sql': log['sql'],
                                                           'reason': log.get('note', '')}, ensure_ascii=False)}]})
    for target, rows in ((path, items), (sft_path, sft)):
        if target:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    return len(items)
