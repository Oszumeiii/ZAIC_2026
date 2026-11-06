"""Quy đổi cụm thời gian tương đối từ `as_of_date` (theo rulebook mẫu: tuần Thứ Hai-Chủ Nhật)."""
from __future__ import annotations

import re

import pandas as pd


def resolve_period(question_norm: str, as_of) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """question_norm: câu hỏi đã `norm()`. Trả (đầu, cuối) hoặc None nếu không nhận ra / cần abstain."""
    t = pd.Timestamp(as_of).normalize()
    qn = question_norm
    if "cung ky" in qn or "so voi" in qn:           # so sánh kỳ: chưa xử lý
        return None
    monday = t - pd.Timedelta(days=t.weekday())
    if re.search(r"tuan (roi|truoc|qua)", qn):
        return monday - pd.Timedelta(days=7), monday - pd.Timedelta(days=1)
    if "tuan nay" in qn:
        return monday, t
    if re.search(r"thang (roi|truoc|qua)", qn):
        first = t.replace(day=1)
        return (first - pd.offsets.MonthBegin(1)), first - pd.Timedelta(days=1)
    if "thang nay" in qn:
        return t.replace(day=1), t
    if "hom qua" in qn:
        return t - pd.Timedelta(days=1), t - pd.Timedelta(days=1)
    if "hom nay" in qn:
        return t, t
    if re.search(r"nam (ngoai|truoc|roi)", qn):
        return pd.Timestamp(t.year - 1, 1, 1), pd.Timestamp(t.year - 1, 12, 31)
    if "nam nay" in qn:
        return pd.Timestamp(t.year, 1, 1), t
    if "quy nay" in qn:
        return pd.Timestamp(t.year, 3 * ((t.month - 1) // 3) + 1, 1), t
    return None


# cụm đã bỏ dấu -> nhãn hiển thị; thứ tự quan trọng (cụm dài trước)
_PHRASES = [("tuan truoc", "tuần trước"), ("tuan roi", "tuần rồi"), ("tuan qua", "tuần qua"), ("tuan nay", "tuần này"),
            ("thang truoc", "tháng trước"), ("thang roi", "tháng rồi"), ("thang qua", "tháng qua"),
            ("thang nay", "tháng này"), ("hom qua", "hôm qua"), ("hom nay", "hôm nay"), ("nam ngoai", "năm ngoái"),
            ("nam truoc", "năm trước"), ("nam roi", "năm rồi"), ("nam nay", "năm nay"), ("quy nay", "quý này")]


def describe_period(question_norm: str, as_of) -> str | None:
    """Một dòng gợi ý khoảng thời gian cho prompt, vd '"tuần trước" = 2026-08-17 đến 2026-08-23'. None nếu không nhận ra."""
    period = resolve_period(question_norm, as_of)
    if period is None:
        return None
    phrase = next((label for key, label in _PHRASES if key in question_norm), None)
    if phrase is None:
        return None
    start, end = period
    return f'"{phrase}" = {start:%Y-%m-%d} đến {end:%Y-%m-%d} (tuần tính Thứ Hai đến Chủ Nhật)'
