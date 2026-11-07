"""Load curated JSON rules once. Matching keys never replace the user's original text."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import unicodedata

from app.text import norm

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "normalization"
FILES = ("temporal_phrases", "business_terms", "common_typos", "slang", "abbreviations")


@dataclass(frozen=True)
class Entry:
    canonical: str
    variants: tuple[str, ...]
    category: str
    safe: bool = True
    context: tuple[str, ...] = ()
    match_canonical: bool = True

    def accepts_accents(self, words: list[str], forms=None) -> bool:
        """Accent folding must not turn a different valid accented word into a variant.

        ASCII words may omit accents; accented words must occur explicitly in a listed
        variant or canonical form. E.g. 'hong'/'hông' can mean 'không', but 'họng' cannot.
        """
        available = forms if forms is not None else (*self.variants, self.canonical)
        # Single ASCII words can already be valid Vietnamese ('vay', 'chua', 'voi').
        # Do not add accents to them solely because a canonical word folds to that key.
        if len(words) == 1 and norm(words[0]) == words[0].casefold():
            if not any(form.casefold() == words[0].casefold() for form in available):
                return False
        for form in available:
            parts = form.split()
            if len(parts) != len(words) or tuple(map(norm, parts)) != tuple(map(norm, words)):
                continue
            if all(norm(word) == word.casefold() or
                   unicodedata.normalize('NFC', word).casefold() == unicodedata.normalize('NFC', part).casefold()
                   for word, part in zip(words, parts)):
                return True
        return False


class Lexicon:
    def __init__(self, directory: Path = DATA_DIR):
        self.entries: list[Entry] = []
        self.counts: dict[str, int] = {}
        self.exact: dict[tuple[str, ...], Entry] = {}
        self.forms: dict[str, list[str]] = {}
        for name in FILES:
            rows = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
            self.counts[name] = len(rows)
            for row in rows:
                entry = Entry(row["canonical"], tuple(row["variants"]), row["category"],
                              row.get("safe", True), tuple(row.get("context", [])), row.get("match_canonical", True))
                self.entries.append(entry)
                self.forms.setdefault(entry.canonical, []).extend((*entry.variants, entry.canonical))
                forms = (*entry.variants, entry.canonical) if entry.match_canonical else entry.variants
                for variant in forms:
                    key = tuple(norm(variant).split())
                    if not key:
                        raise ValueError(f"Empty normalization key: {name}")
                    old = self.exact.get(key)
                    if old is not None and old.canonical != entry.canonical:
                        raise ValueError(f"Conflicting normalization rule: {variant!r}")
                    self.exact.setdefault(key, entry)
        rows = json.loads((directory / "ambiguous_terms.json").read_text(encoding="utf-8"))
        self.counts["ambiguous_terms"] = len(rows)
        self.ambiguous = {norm(v): row for row in rows for v in row["variants"]}
        if any((key,) in self.exact for key in self.ambiguous):
            raise ValueError("Ambiguous abbreviations must not have unconditional rules")
        self.max_words = max(map(len, self.exact))


@lru_cache(maxsize=1)
def default_lexicon() -> Lexicon:
    return Lexicon()
