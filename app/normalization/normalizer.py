"""Preserve intent, codes and entities; expand only supported linguistic variants.

Offsets in metadata refer to the Unicode-cleaned input (not raw byte offsets).
Fuzzy scores are similarity heuristics, not calibrated probabilities.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

from app.text import norm

from .context_rules import has_signal, resolve
from .dictionary import Lexicon, default_lexicon
from .fuzzy_matcher import FuzzyMatcher

TOKEN = re.compile(r"\w+", re.UNICODE)
QUOTED = r"`[^`]*`|'[^'\n]*'|\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’"
LITERAL = re.compile(QUOTED + r"|\b[\w./:+%-]*\d[\w./:+%-]*\b", re.UNICODE)
INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


@dataclass
class NormalizationResult:
    original_question: str
    normalized_question: str
    corrections: list[dict] = field(default_factory=list)
    unresolved_terms: list[dict] = field(default_factory=list)

    @property
    def has_ambiguity(self) -> bool:
        return bool(self.unresolved_terms)

    def to_dict(self) -> dict:
        return {**asdict(self), "has_ambiguity": self.has_ambiguity}

    def prompt_section(self, use_hints: bool = True) -> str:
        lines = ["CÂU HỎI ĐÃ CHUẨN HÓA:", self.normalized_question]
        if use_hints and self.corrections:
            lines += ["GỢI Ý CHUẨN HÓA:"]
            lines += [f"- {c['original'][:72]} -> {c['replacement'][:72]}" for c in self.corrections[:8]]
            if len(self.corrections) > 8:
                lines.append("- (các sửa đổi còn lại đã thể hiện trong câu chuẩn hóa)")
        if self.unresolved_terms:
            lines.append("TỪ/CỤM TỪ CHƯA XÁC ĐỊNH:")
            for term in self.unresolved_terms[:8]:
                choices = " / ".join(c["replacement"] for c in term["candidates"])
                lines.append(f"- {term['original']}: {choices} (chưa kết luận)")
        return "\n".join(lines)


def basic_normalize(text: str) -> str:
    # Keep quoted literals, numeric expressions and SQL fragments byte-for-byte.
    pieces, cursor = [], 0
    for match in re.finditer(QUOTED, text):
        pieces.append(_clean(text[cursor:match.start()]))
        pieces.append(match.group())
        cursor = match.end()
    pieces.append(_clean(text[cursor:]))
    return "".join(pieces).strip()


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", INVISIBLE.sub("", text))
    text = text.translate(str.maketrans({"，": ",", "？": "?", "！": "!", "：": ":"}))
    return re.sub(r"\s+", " ", text)


class QuestionNormalizer:
    def __init__(self, lexicon: Lexicon | None = None, **fuzzy_options):
        self.lexicon = lexicon or default_lexicon()
        self.fuzzy = FuzzyMatcher(self.lexicon, **fuzzy_options)

    def normalize(self, question: str, *, schema: str = "", readme: str = "",
                  protected_values=(), grounded_phrases=()) -> NormalizationResult:
        text = basic_normalize(question)
        result = NormalizationResult(question, text)
        if text != question:
            result.corrections.append({"original": question, "replacement": text, "method": "unicode_whitespace"})
        tokens = list(TOKEN.finditer(text))
        keys = [norm(m.group()) for m in tokens]
        domain = norm(schema + " " + readme).replace("_", " ")
        protected = [(m.start(), m.end()) for m in LITERAL.finditer(text)
                     if norm(m.group()) not in self.lexicon.ambiguous]
        protected += [m.span() for m in re.finditer(
            r"https?://\S+|[\w.+-]+@[\w.-]+\.\w+|\b\w+(?:[-–—_/.:]\w+)+\b|\b\w+\s*(?:!=|<=|>=|<>|=|<|>)\s*[^\s,;]+", text)]
        protected += [m.span() for m in re.finditer(r"\b\w+\s+(?:[+*/-])\s+\w+\b", text)]
        # SQL is data, not a Vietnamese sentence to rewrite.
        sql = re.search(r"\b(?:SELECT\s+.+?\s+FROM|UPDATE\s+\w+\s+SET|DELETE\s+FROM|INSERT\s+INTO|WHERE\s+\w+\s*[=<>]|(?:SUM|COUNT|AVG)\s*\().*", text, re.I)
        if sql:
            protected.append(sql.span())
        # Existing ValueIndex supplies the values and already-grounded entity phrases.
        value_keys = {tuple(norm(m.group()) for m in TOKEN.finditer(unicodedata.normalize("NFC", str(v))))
                      for v in (*protected_values, *grounded_phrases) if str(v).strip()}
        max_entity = max((len(k) for k in value_keys), default=0)
        for i in range(len(tokens)):
            for n in range(min(max_entity, len(tokens) - i), 0, -1):
                if tuple(keys[i:i+n]) in value_keys:
                    protected.append((tokens[i].start(), tokens[i+n-1].end()))
                    break
        # Explicit entity designators protect a following short identifier (e.g. "kh A", "mã SP").
        for i in range(1, len(tokens)):
            if keys[i-1] in {"ma", "code", "id"} or (keys[i-1] in {"kh", "sp"} and len(tokens[i].group()) == 1):
                protected.append(tokens[i].span())

        replacements = []
        i = 0
        while i < len(tokens):
            if self._overlaps(tokens[i].start(), tokens[i].end(), protected):
                i += 1
                continue
            chosen = None
            for n in range(min(self.lexicon.max_words, len(tokens) - i), 0, -1):
                start, end = tokens[i].start(), tokens[i+n-1].end()
                if self._overlaps(start, end, protected) or not self._contiguous(text, tokens, i, n):
                    continue
                entry = self.lexicon.exact.get(tuple(keys[i:i+n]))
                local = norm(text[max(0, start-60):min(len(text), end+60)])
                if (entry and entry.safe and entry.accepts_accents([t.group() for t in tokens[i:i+n]], self.lexicon.forms[entry.canonical])
                        and (not entry.context or has_signal(local, entry.context))):
                    chosen = (n, entry.canonical, "phrase_dictionary" if n > 1 else "dictionary")
                    break
            if chosen is None and keys[i] in self.lexicon.ambiguous:
                entry = self.lexicon.ambiguous[keys[i]]
                start, end = tokens[i].span()
                local = norm(text[max(0, start-50):min(len(text), end+50)])
                resolved = resolve(entry, local, domain)
                # "k" after a number means a unit; never negate a monetary value.
                if keys[i] == "k" and i > 0 and any(c.isdigit() for c in tokens[i-1].group()):
                    resolved = None
                if resolved:
                    chosen = (1, resolved, "context_rule")
                else:
                    result.unresolved_terms.append({"original": tokens[i].group(), "start": start, "end": end,
                                                    "method": "ambiguous_dictionary",
                                                    "candidates": [{"replacement": c["canonical"]} for c in entry["candidates"]]})
                    i += 1
                    continue
            if chosen is None:
                # Unknown phrases first. No fuzzy rewrite based on similarity alone.
                pending = None
                for n in range(min(3, len(tokens)-i), 0, -1):
                    start, end = tokens[i].start(), tokens[i+n-1].end()
                    if self._overlaps(start, end, protected) or not self._contiguous(text, tokens, i, n):
                        continue
                    chunk = text[start:end]
                    if any(t.group()[:1].isupper() for t in tokens[i:i+n]):
                        continue  # Unknown capitalized names are not fuzzy spelling targets.
                    if tuple(keys[i:i+n]) in self.lexicon.exact or any(k in self.lexicon.ambiguous for k in keys[i:i+n]):
                        continue
                    if all(k in self.fuzzy.known_words for k in keys[i:i+n]):
                        continue
                    candidates = self.fuzzy.candidates(chunk)
                    if not candidates:
                        continue
                    best = candidates[0]
                    entry = self.fuzzy.vocabulary[best["replacement"]]
                    outside = norm(text[max(0, start-60):start] + " " + text[end:min(len(text), end+60)])
                    # A longer matching phrase needs a correct shared anchor; a word needs a curated local signal.
                    shared = set(keys[i:i+n]) & set(norm(entry.canonical).split())
                    evidence = bool(entry.context and has_signal(outside, entry.context))
                    supported = has_signal(domain, [entry.canonical, *entry.context])
                    if self.fuzzy.confident(chunk, candidates) and supported and (evidence or (n > 1 and shared)):
                        chosen = (n, entry.canonical, "fuzzy_context")
                        break
                    # Keep candidate metadata compact; short/known/correct words are not flagged.
                    if pending is None and len(chunk) >= 5 and best["score"] >= .80:
                        pending = {"original": chunk, "start": start, "end": end,
                                   "method": "fuzzy_candidates", "candidates": candidates}
                if chosen is None and pending is not None:
                    result.unresolved_terms.append(pending)
            if chosen:
                n, replacement, method = chosen
                start, end = tokens[i].start(), tokens[i+n-1].end()
                original = text[start:end]
                # Retain existing casing for already-correct text; expansions follow sentence casing.
                if norm(original) == norm(replacement) and unicodedata.normalize("NFC", original).lower() == replacement.lower():
                    replacement = original
                elif original[:1].isupper() and not original.isupper():
                    replacement = replacement[:1].upper() + replacement[1:]
                if replacement != original:
                    replacements.append((start, end, replacement))
                    result.corrections.append({"original": original, "replacement": replacement, "method": method,
                                               "start": start, "end": end})
                i += n
            else:
                i += 1
        for start, end, replacement in reversed(replacements):
            text = text[:start] + replacement + text[end:]
        result.normalized_question = text
        return result

    @staticmethod
    def _contiguous(text, tokens, i, n):
        return all(text[tokens[j].end():tokens[j+1].start()].isspace() for j in range(i, i+n-1))

    @staticmethod
    def _overlaps(start, end, spans):
        return any(start < b and end > a for a, b in spans)
