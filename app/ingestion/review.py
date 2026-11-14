"""Build split reports from stored catalogs, without reopening source spreadsheets."""
import json
from pathlib import Path
from app.ingestion.catalog import CatalogService


def build_review(directory):
    directory = Path(directory)
    summary = directory / 'summary.json'
    previous = json.loads(summary.read_text()).get('businesses', []) if summary.exists() else []
    failed = {b['business_id']: b for b in previous if b.get('status') == 'failed'}
    businesses = list(failed.values())
    for database in sorted(directory.glob('*/data.duckdb')):
        if database.parent.name == '_shared' or database.parent.name in failed:
            continue
        catalog = CatalogService.read_catalog(database)
        businesses.append({'business_id': catalog['business_id'], 'status': 'complete',
                           'tables': len(catalog['tables']),
                           'rows': sum(t['row_count'] for t in catalog['tables'])})
    report = {'businesses': businesses, 'totals': {
        k: sum(b.get(k, 0) for b in businesses) for k in ('tables', 'rows')}}
    for name in ('review.json', 'summary.json'):
        (directory / name).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (directory / 'REVIEW.md').write_text('# Ingestion review\n\n' + '\n'.join(
        f"- {b['business_id']}: {b['status']}; {b.get('tables', 0)} tables, {b.get('rows', 0)} rows" for b in businesses))
    return report
