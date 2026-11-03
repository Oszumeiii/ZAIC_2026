"""Dựng ngữ cảnh cho một câu hỏi: rulebook + README + profile cột, tự rút gọn cho vừa ngân sách token."""
from __future__ import annotations

from typing import Callable

from app.config import Config
from app.ingestion.dataset import Business, Split
from app.ingestion.profiling import merge_same_schema, profile_min, profile_text

# mỗi bước: (tổng ký tự profile, ký tự mỗi bảng, có head(3) không, số giá trị phân loại liệt kê tối đa)
_FIT_STEPS_TAIL = [(10000, 2000, True, 12), (6000, 1200, False, 8), (3500, 700, False, 5)]


class ContextBuilder:
    """Dựng ngữ cảnh cho một câu hỏi. `count_tokens` (chỉ local) để rút gọn vừa ngân sách token."""

    def __init__(self, cfg: Config, count_tokens: Callable[[str], int] | None = None):
        self.cfg = cfg
        self.count_tokens = count_tokens
        self.rulebook_limit, self.readme_limit, self.profile_limit = cfg.context_limits
        steps = [(self.profile_limit, 4000, True, 25)] + _FIT_STEPS_TAIL
        self.steps = steps if cfg.is_local else steps[:1]
        self._warned: set = set()

    def _warn_once(self, key, msg: str):
        if key not in self._warned:
            self._warned.add(key)
            print(msg)

    def _prepare(self, biz: Business, calendar):
        """Gộp sheet cùng cấu trúc một lần cho mỗi business."""
        if "merged" not in biz.cache:
            merged, notes = merge_same_schema(biz.tables)
            if calendar is not None:
                merged["calendar"] = calendar
            biz.cache.update(merged=merged, merge_notes=notes, profiles={})

    @staticmethod
    def _build(question, as_of, biz: Business, rulebook, profile, rulebook_chars, readme_chars) -> str:
        notes = biz.cache["merge_notes"]
        return (f"RULEBOOK:\n{rulebook[:rulebook_chars]}\n\nREADME:\n{biz.readme[:readme_chars]}\n\n"
                f"SCHEMA + PROFILE:\n{profile}\n\n"
                + ("LƯU Ý: " + " ".join(notes) + "\n\n" if notes else "") +
                f"AS_OF: {as_of}\nCÂU HỎI: {question}")

    def build(self, question: str, as_of, biz: Business, split: Split) -> tuple[str, dict]:
        """Trả (ngữ cảnh, bảng đã gộp để chạy code)."""
        self._prepare(biz, split.calendar)
        tables = dict(biz.cache["merged"])
        profiles = biz.cache["profiles"]
        rulebook = split.rulebook
        for name, text, limit in (("rulebook", rulebook, self.rulebook_limit), ("readme", biz.readme, self.readme_limit)):
            if len(text) > limit:
                self._warn_once(name, f"⚠ {name} dài {len(text)} ký tự, bị cắt còn {lim}")
        budget = self.cfg.token_budget
        counter = self.count_tokens if budget is not None else None
        ctx = ""
        for si, (plim, ptab, head, mcat) in enumerate(self.steps):
            if si not in profiles:
                profiles[si] = profile_text(tables, plim, ptab, head, mcat)
            ctx = self._build(question, as_of, biz, rulebook, profiles[si], self.rulebook_limit, self.readme_limit)
            if counter is None or counter(ctx) <= budget:
                return ctx, tables
        # vẫn dài: cắt dần rulebook/README, cuối cùng chỉ giữ tên bảng + cột
        last = profiles[len(self.steps) - 1]
        fallbacks = ((self.rulebook_limit // 2, 1000, last), (self.rulebook_limit // 4, 0, last),
                     (1500, 0, profiles.setdefault("min", profile_min(tables))))
        n = 0
        for rb, rd, prof in fallbacks:
            ctx = self._build(question, as_of, biz, rulebook, prof, rb, rd)
            n = counter(ctx)
            if n <= budget:
                return ctx, tables
        self._warn_once(id(biz), f"prompt {n} token > ngân sách {budget} dù đã rút gọn hết mức; tăng max_model_len")
        return ctx, tables
