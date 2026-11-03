"""Test quy đổi thời gian tương đối."""
import pandas as pd

from app.text import norm
from app.tools.timeutil import resolve_period


def test_resolve_period_week_and_month():
    qn = norm("tuần trước")
    assert resolve_period(qn, "2026-08-27") == (pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-23"))
    assert resolve_period(norm("tháng trước"), "2026-08-27") == (pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"))
    assert resolve_period(norm("tháng này"), "2026-08-27") == (pd.Timestamp("2026-08-01"), pd.Timestamp("2026-08-27"))
    assert resolve_period(norm("so với cùng kỳ"), "2026-08-27") is None
