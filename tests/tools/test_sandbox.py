"""Test chạy code có giới hạn thời gian."""
import pandas as pd
import pytest

from app.tools.sandbox import CodeTimeout, run_code


def test_sandbox_runs_and_times_out():
    tables = {"t": pd.DataFrame({"x": [1, 2]})}
    assert run_code('result_type="number"\nresult=int(tables["t"]["x"].sum())', tables, "2026-08-27") == ("number", 3)
    with pytest.raises(CodeTimeout):
        run_code("while True: pass", tables, "2026-08-27", secs=1)
