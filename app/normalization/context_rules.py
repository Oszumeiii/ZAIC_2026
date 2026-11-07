"""Conservative ambiguity decisions using local wording AND independent schema/README evidence."""
from __future__ import annotations

import re

from app.text import norm


def contains(text: str, phrase: str) -> bool:
    key = norm(phrase).replace("_", " ")
    return bool(re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", text))


def has_signal(text: str, signals) -> bool:
    return any(contains(text, s) for s in signals)


def resolve(entry: dict, local: str, schema: str) -> str | None:
    """A unique meaning requires both local and domain signals; ties remain unresolved."""
    choices = [c["canonical"] for c in entry["candidates"]
               if c.get("context") and c.get("schema")
               and has_signal(local, c["context"]) and has_signal(schema, c["schema"])]
    return choices[0] if len(choices) == 1 else None
