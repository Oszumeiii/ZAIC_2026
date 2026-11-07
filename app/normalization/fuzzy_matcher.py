"""Standard-library fuzzy candidates. Similarity is a heuristic, never semantic evidence."""
from __future__ import annotations

from difflib import SequenceMatcher

from app.text import norm

from .dictionary import Lexicon


class FuzzyMatcher:
    def __init__(self, lexicon: Lexicon, word_threshold: float = .91, phrase_threshold: float = .90,
                 short_threshold: float = .96, margin: float = .08):
        for value in (word_threshold, phrase_threshold, short_threshold, margin):
            if not 0 <= value <= 1:
                raise ValueError("Similarity thresholds and margin must lie in [0, 1]")
        self.word_threshold, self.phrase_threshold = word_threshold, phrase_threshold
        self.short_threshold, self.margin = short_threshold, margin
        self.vocabulary = {e.canonical: e for e in lexicon.entries if e.safe}
        self.known_words = {word for key in lexicon.exact for word in key}
        self.known_words.update(word for entry in lexicon.entries for word in norm(entry.canonical).split())
        self.buckets: dict[int, list[tuple[str, str]]] = {}
        for canonical in self.vocabulary:
            key = norm(canonical)
            self.buckets.setdefault(len(key.split()), []).append((canonical, key))

    def candidates(self, phrase: str, limit: int = 3) -> list[dict]:
        key = norm(phrase)
        if len(key) < 4 or any(ch.isdigit() for ch in key):
            return []
        found = []
        for canonical, target in self.buckets.get(len(key.split()), []):
            if abs(len(key) - len(target)) > max(2, len(target) // 4):
                continue
            score = SequenceMatcher(None, key, target, autojunk=False).ratio()
            if score >= .75:
                found.append({"replacement": canonical, "score": round(score, 4)})
        return sorted(found, key=lambda c: (-c["score"], c["replacement"]))[:limit]

    def confident(self, phrase: str, candidates: list[dict]) -> bool:
        if not candidates:
            return False
        key = norm(phrase)
        threshold = (self.short_threshold if len(key) <= 4 else
                     self.phrase_threshold if " " in key else self.word_threshold)
        return (candidates[0]["score"] >= threshold and
                (len(candidates) == 1 or candidates[0]["score"] - candidates[1]["score"] >= self.margin))
