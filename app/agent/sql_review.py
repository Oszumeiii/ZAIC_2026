"""A separate local review pass plus executable read-only business assertions."""
import json
from app.tools.sql import SQLExecutor

DIMENSIONS = ('grain', 'joins', 'time', 'units', 'rules')
REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'properties': {
        'checks': {'type': 'object', 'additionalProperties': False,
                   'properties': {k: {'type': 'string', 'enum': ['pass', 'fail', 'unknown']} for k in DIMENSIONS},
                   'required': list(DIMENSIONS)},
        'reason': {'type': 'string', 'minLength': 1},
        'required_tables': {'type': 'array', 'items': {'type': 'string'}},
        'assertions': {'type': 'array', 'maxItems': 5, 'items': {'type': 'object',
            'properties': {'sql': {'type': 'string'}, 'reason': {'type': 'string'}},
            'required': ['sql', 'reason'], 'additionalProperties': False}}},
    'required': ['checks', 'reason', 'required_tables', 'assertions']}
REVIEW_SYSTEM = '''Independently audit a proposed DuckDB answer against the original question, README,
rulebook and catalog. Do not assume successful execution or majority agreement means correctness.
Check grain, join fanout/double counting, date role/as_of boundaries, unit conversions and business rules.
For joins with aggregation, require executable assertions proving join cardinality or conservation of the
measure at its original grain. Assertions must each return exactly one BOOLEAN cell; true means pass.
Do not use tautologies. Check actual key uniqueness, multiplicity, data coverage and relevant totals.
For unanswerable, verify missing evidence, not merely missing tables from the shortened context.
required_tables is ONLY a request to restore missing schemas, not a list of tables used by SQL.
Use exact physical names from context.omitted_tables only; return [] if no omitted schema is needed.
Never request tables already present in context.tables, calendar or semantic_views.
Mark unknown when evidence is insufficient, even if all table schemas are present.
Never invent missing evidence. Return checks, reason, required_tables, assertions as JSON.'''


def verify(service, connection, context, plan, columns, rows):
    payload = {'context': context, 'plan': plan, 'result_columns': columns,
               'result_preview': rows[:10], 'result_row_count': len(rows)}
    report = service.json(REVIEW_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str),
                          REVIEW_SCHEMA, task='review')
    # Models sometimes echo every SQL dependency here. Only omitted schemas can
    # expand the context; already visible dependencies must still pass all checks.
    available = {table['name'] for table in context.get('tables', [])}
    if context.get('calendar'):
        available.add(context['calendar']['name'])
    available.update(f"{view['schema']}.{view['name']}" for view in context.get('semantic_views', []))
    omitted = set(context.get('omitted_tables', [])) - available
    requested = set(report['required_tables'])
    unknown = requested - available - omitted
    if unknown:
        raise ValueError(f"Unknown required tables: {sorted(unknown)}; "
                         "use exact physical names from omitted_tables only")
    report = {**report, 'required_tables': sorted(requested & omitted)}
    if report['required_tables']:
        return report
    if any(v != 'pass' for v in report['checks'].values()):
        raise ValueError('Business review failed: ' + report['reason'])
    import re
    if re.search(r'\bjoin\b', plan['sql'], re.I) and re.search(r'\b(sum|avg|count)\s*\(', plan['sql'], re.I) and not report['assertions']:
        raise ValueError('JOIN aggregation requires grain/cardinality assertions')
    for assertion in report['assertions']:
        _, result = SQLExecutor.query(connection, assertion['sql'], timeout=service.cfg.code_timeout_s)
        if len(result) != 1 or len(result[0]) != 1 or type(result[0][0]) is not bool or not result[0][0]:
            raise ValueError('Business assertion failed: ' + assertion['reason'])
    return report
