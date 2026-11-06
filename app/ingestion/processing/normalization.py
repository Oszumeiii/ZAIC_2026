from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence
import re
import unicodedata

NUMBER_PATTERN = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
GROUPED_NUMBER_PATTERN = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


class IdentifierNormalizer:
    """Normalize source headers, physical identifiers and search aliases."""

    @staticmethod
    def aliases(source_name: str | None, description: str | None) -> list[str]:
        aliases: list[str] = []
        for candidate in (source_name, description):
            if candidate and candidate.strip() and candidate.strip() not in aliases:
                aliases.append(candidate.strip())
        return aliases

    @staticmethod
    def normalize(value: str, *, fallback: str = "column") -> str:
        value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
        value = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", value)
        normalized = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
        normalized = "".join(
            character
            for character in normalized
            if not unicodedata.combining(character)
        )
        normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
        if not normalized:
            normalized = fallback
        if normalized[0].isdigit():
            normalized = f"col_{normalized}"
        return normalized

    @staticmethod
    def unique(names: Sequence[str | None]) -> tuple[str, ...]:
        result: list[str] = []
        seen: dict[str, int] = {}
        for index, name in enumerate(names, start=1):
            base = IdentifierNormalizer.normalize(name or "", fallback=f"column_{index}")
            seen[base] = seen.get(base, 0) + 1
            result.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
        return tuple(result)

    @staticmethod
    def headers(values: Sequence[Any]) -> tuple[str | None, ...]:
        return tuple(
            None if value is None or not str(value).strip() else str(value)
            for value in values
        )


class ValueNormalizer:
    """Infer storage types and normalize values without losing identifiers."""

    @staticmethod
    def _parse_numeric_text(value: str) -> int | float | None:
        text = value.strip()
        if GROUPED_NUMBER_PATTERN.fullmatch(text):
            text = text.replace(",", "")
        elif not NUMBER_PATTERN.fullmatch(text):
            return None

        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        if not number.is_finite():
            return None
        if number == number.to_integral_value():
            return int(number)
        return float(number)

    @staticmethod
    def _normalized_value(value: Any) -> Any:
        if isinstance(value, str):
            parsed = ValueNormalizer._parse_numeric_text(value)
            return parsed if parsed is not None else value
        return value

    @staticmethod
    def infer_type(values: Iterable[Any], *, force_text: bool = False) -> str:
        if force_text:
            return "VARCHAR"

        observed = [ValueNormalizer._normalized_value(value) for value in values if value is not None]
        if not observed:
            return "VARCHAR"
        if all(isinstance(value, bool) for value in observed):
            return "BOOLEAN"
        if all(isinstance(value, int) and not isinstance(value, bool) for value in observed):
            if all(-(2**63) <= value < 2**63 for value in observed):
                return "BIGINT"
            return "VARCHAR"
        if all(
            isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
            for value in observed
        ):
            return "DOUBLE"
        if all(isinstance(value, datetime) for value in observed):
            return "TIMESTAMP"
        if all(isinstance(value, date) and not isinstance(value, datetime) for value in observed):
            return "DATE"
        return "VARCHAR"

    @staticmethod
    def normalize(value: Any, duckdb_type: str) -> Any:
        if value is None:
            return None
        if duckdb_type == "VARCHAR":
            if isinstance(value, datetime):
                return value.isoformat(sep=" ")
            if isinstance(value, date):
                return value.isoformat()
            return str(value)
        if duckdb_type in {"BIGINT", "DOUBLE"}:
            normalized = ValueNormalizer._normalized_value(value)
            if isinstance(normalized, bool) or not isinstance(
                normalized, (int, float, Decimal)
            ):
                raise ValueError(f"Value {value!r} cannot be converted to {duckdb_type}.")
            return int(normalized) if duckdb_type == "BIGINT" else float(normalized)
        if duckdb_type == "TIMESTAMP" and isinstance(value, datetime):
            return value
        if duckdb_type == "DATE" and isinstance(value, date):
            return value
        if duckdb_type == "BOOLEAN" and isinstance(value, bool):
            return value
        raise ValueError(f"Value {value!r} cannot be converted to {duckdb_type}.")

    @staticmethod
    def is_identifier(semantic_name: str | None, source_name: str | None) -> bool:
        if source_name and re.match(r"^(?:mã|ma)[A-Z]", source_name, flags=re.IGNORECASE):
            return True
        tokens = {
            token
            for value in (semantic_name, source_name)
            if value
            for token in re.split(r"[^a-z0-9]+", value.casefold())
            if token
        }
        return bool(tokens.intersection({"id", "code", "ma", "mã", "key", "uuid"}))
