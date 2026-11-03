from itertools import islice
from pathlib import Path
from typing import Iterable, Iterator

from openpyxl import load_workbook
import xlrd

from app.ingestion.adapters.common import SourceTable, TabularLayout
from app.ingestion.normalization import IdentifierNormalizer

HEADER_SCAN_ROWS = 50


class ExcelAdapter:
    """Stream workbook sheets from XLS and XLSX sources."""

    @staticmethod
    def read(
        path: Path,
        source_file: str,
        expected_headers: Iterable[str] = (),
    ) -> Iterator[SourceTable]:
        if path.suffix.casefold() == ".xls":
            yield from ExcelAdapter._read_xls(path, source_file, expected_headers)
            return

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for worksheet in workbook.worksheets:
                numbered_rows = (
                    (row_number, tuple(values))
                    for row_number, values in enumerate(
                        worksheet.iter_rows(values_only=True), start=1
                    )
                    if TabularLayout.nonempty_row(values)
                )
                buffered = list(islice(numbered_rows, HEADER_SCAN_ROWS))
                if not buffered:
                    continue
                header_number, header_values, header_status = TabularLayout.choose_header(
                    buffered, expected_headers
                )
                headers = IdentifierNormalizer.headers(header_values)
                remaining = (
                    (row_number, values) for row_number, values in numbered_rows
                )
                yield SourceTable(
                    source_file=source_file,
                    source_object=worksheet.title,
                    source_headers=headers,
                    rows=TabularLayout.prepare_rows(
                        buffered, remaining, header_number, len(headers)
                    ),
                    header_row_number=header_number,
                    header_status=header_status,
                )
        finally:
            workbook.close()

    @staticmethod
    def _read_xls(
        path: Path,
        source_file: str,
        expected_headers: Iterable[str],
    ) -> Iterator[SourceTable]:
        workbook = xlrd.open_workbook(path.as_posix(), on_demand=True)
        try:
            for worksheet in workbook.sheets():

                numbered_rows = ExcelAdapter._rows_for_sheet(worksheet, workbook.datemode)
                buffered = list(islice(numbered_rows, HEADER_SCAN_ROWS))
                if not buffered:
                    continue
                header_number, header_values, header_status = TabularLayout.choose_header(
                    buffered, expected_headers
                )
                headers = IdentifierNormalizer.headers(header_values)
                remaining = (
                    (row_number, values) for row_number, values in numbered_rows
                )
                yield SourceTable(
                    source_file=source_file,
                    source_object=worksheet.name,
                    source_headers=headers,
                    rows=TabularLayout.prepare_rows(buffered, remaining, header_number, len(headers)),
                    header_row_number=header_number,
                    header_status=header_status,
                )
        finally:
            workbook.release_resources()

    @staticmethod
    def _xls_row_values(worksheet: xlrd.sheet.Sheet, row_index: int, datemode: int) -> tuple[object, ...]:
        if datemode not in (0, 1):
            raise ValueError(f"Unsupported Excel date mode: {datemode}.")
        values = []
        for cell in worksheet.row(row_index):
            if cell.ctype == xlrd.XL_CELL_DATE:
                if not isinstance(cell.value, (int, float)):
                    raise ValueError(
                        f"Excel date cell at row {row_index + 1} is not numeric."
                    )
                values.append(
                    xlrd.xldate.xldate_as_datetime(float(cell.value), datemode)
                )
            elif cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                values.append(None)
            else:
                values.append(cell.value)
        return tuple(values)

    @staticmethod
    def _rows_for_sheet(worksheet, datemode) -> Iterator[tuple[int, tuple[object, ...]]]:
        for row_number in range(1, worksheet.nrows + 1):
            values = ExcelAdapter._xls_row_values(
                worksheet, row_number - 1, datemode
            )
            if TabularLayout.nonempty_row(values):
                yield row_number, values
