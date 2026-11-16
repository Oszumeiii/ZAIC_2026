"""Capture generated reasoning and actual SQL calls without summarizing their contents."""
from contextlib import contextmanager
import re
import time

from app.llm.base import InferenceBudgetExceeded


def thinking_in(text):
    matches = re.findall(r'<think>(.*?)(?:</think>|\Z)', text, flags=re.S)
    return matches


class AuditedBackend:
    def __init__(self, backend):
        self.backend, self.calls = backend, []
        self.set_budget()

    def set_budget(self, *, deadline=None, max_calls=None):
        self.deadline, self.max_calls, self.used_calls = deadline, max_calls, 0

    def check_budget(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise InferenceBudgetExceeded('Inference time budget exhausted')
        if self.max_calls is not None and self.used_calls >= self.max_calls:
            raise InferenceBudgetExceeded('Inference model-call budget exhausted')

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def generate_with_options(self, system, prompt, **options):
        self.check_budget()
        self.used_calls += 1
        started = time.monotonic()
        call = {'system': system, 'prompt': prompt, 'options': options}
        self.calls.append(call)
        try:
            outputs = self.backend.generate_with_options(system, prompt, **options)
            call['responses'] = outputs
            separate = getattr(self.backend, 'last_reasoning', None)
            call['thinking'] = [v for v in separate if v is not None] if separate else [
                thought for output in outputs for thought in thinking_in(output)]
            return outputs
        except Exception as error:
            call['error'] = repr(error)
            raise
        finally:
            call['elapsed_ms'] = max(0, int((time.monotonic() - started) * 1000))


@contextmanager
def record_sql():
    from app.tools.sql import SQLExecutor
    original = SQLExecutor.query
    calls = []
    def query(connection, sql, *args, **kwargs):
        call = {'code': sql, 'function': 'app/tools/sql.py::SQLExecutor.query'}
        calls.append(call)
        try:
            columns, rows = original(connection, sql, *args, **kwargs)
            call['output'] = {'columns': columns, 'rows': rows}
            return columns, rows
        except Exception as error:
            call['error'] = repr(error)
            raise
    SQLExecutor.query = staticmethod(query)
    try:
        yield calls
    finally:
        SQLExecutor.query = staticmethod(original)
