from dataclasses import dataclass
from itertools import chain
from typing import Any, Iterable, Iterator, Sequence


@dataclass(slots=True)
class SourceTable:
    source_file: str
    source_object: str
    source_headers: tuple[str | None, ...]
    rows: Iterator[tuple[Any, ...]]
    header_row_number: int | None
    header_status: str
    source_types: tuple[str | None, ...] = ()
    relationships: tuple[dict[str, Any], ...] = ()


class TabularLayout:
    """Select headers and stream rows using shared tabular layout rules."""

    @staticmethod
    def quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    def nonempty_row(values: Sequence[Any]) -> bool:
        return any(value is not None and str(value).strip() for value in values)

    @staticmethod
    def prepare_rows(
        buffered_rows: Sequence[tuple[int, tuple[Any, ...]]],
        remaining_rows: Iterable[tuple[int, tuple[Any, ...]]],
        header_index: int,
        width: int,
    ) -> Iterator[tuple[Any, ...]]:
        for row_number, values in chain(buffered_rows, remaining_rows):
            if row_number <= header_index or not TabularLayout.nonempty_row(values):
                continue
            if len(values) > width and any(value is not None for value in values[width:]):
                raise ValueError(
                    f"Row {row_number} has {len(values)} cells but the header has "
                    f"only {width} columns."
                )
            yield tuple(values[index] if index < len(values) else None for index in range(width))

    @staticmethod
    def choose_header(
        candidates: Sequence[tuple[int, tuple[Any, ...]]],
        expected_headers: Iterable[str],
    ) -> tuple[int, tuple[Any, ...], str]:
        if not candidates:
            raise ValueError("Source table contains no non-empty rows.")

        expected = {value.strip().casefold() for value in expected_headers if value.strip()}
        if expected:
            scored = [
                (
                    sum(
                        1
                        for value in values
                        if value is not None and str(value).strip().casefold() in expected
                    ),
                    sum(
                        1
                        for value in values
                        if value is not None and str(value).strip()
                    ),
                    row_number,
                    values,
                )
                for row_number, values in candidates
            ]
            best = max(scored, key=lambda item: (item[0], item[1], -item[2]))
            if best[0] > 0:
                return best[2], best[3], "manifest_header_match"


        best_candidate = max(candidates, key=TabularLayout._header_score)
        if len([value for value in best_candidate[1] if isinstance(value, str) and value.strip()]) >= 2:
            status = "heuristic_header"
        else:
            status = "first_nonempty_row"
        return best_candidate[0], best_candidate[1], status

    @staticmethod
    def _header_score(candidate: tuple[int, tuple[Any, ...]]) -> tuple[int, float, int]:
        row_number, values = candidate
        strings = [
            str(value).strip()
            for value in values
            if isinstance(value, str) and value.strip()
        ]
        normalized = [value.casefold() for value in strings]
        unique_ratio = len(set(normalized)) / len(normalized) if normalized else 0.0
        return len(strings), unique_ratio, -row_number
