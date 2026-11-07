"""Transactional ordering for dependent stage-2 and stage-3 views."""
from . import build, model
from ..readers import quote_identifier as q


def organize_connection(con, analysis):
    # Models can reference merged stage-2 views. Remove our models first, rebuild
    # both layers, then check dependencies (including user views) once complete.
    model.cleanup_models(con)
    analysis['stage2'] = build.build_connection(con, analysis, validate_catalog=False)
    analysis['stage3'] = model.build_models(con, analysis)
    for view in build.catalog(con):
        con.execute('SELECT * FROM semantic.' + q(view) + ' LIMIT 0')
