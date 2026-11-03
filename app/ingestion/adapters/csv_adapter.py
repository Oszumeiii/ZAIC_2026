from itertools import islice
from pathlib import Path
from typing import Iterable
import csv

from app.ingestion.adapters.common import SourceTable, TabularLayout
from app.ingestion.normalization import IdentifierNormalizer

HEADER_SCAN_ROWS = 50


class CsvAdapter:
    """Read CSV sources while preserving header and row provenance."""

    @staticmethod
    def read(
        path: Path,
        source_file: str,
        expected_headers: Iterable[str] = (),
    ) -> SourceTable:
        file_handle = path.open("r", encoding="utf-8-sig", newline="")
        try:
            sample = file_handle.read(8192)
            file_handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.reader(file_handle, dialect)

            rows = CsvAdapter._numbered_rows(reader, file_handle)
            buffered = list(islice(rows, HEADER_SCAN_ROWS))
            header_number, header_values, header_status = TabularLayout.choose_header(
                buffered, expected_headers
            )
            headers = IdentifierNormalizer.headers(header_values)
            remaining = ((row_number, values) for row_number, values in rows)
            return SourceTable(
                source_file=source_file,
                source_object=path.stem,
                source_headers=headers,
                rows=TabularLayout.prepare_rows(buffered, remaining, header_number, len(headers)),
                header_row_number=header_number,
                header_status=header_status,
            )
        except Exception:
            file_handle.close()
            raise

    @staticmethod
    def _numbered_rows(reader, file_handle):
        try:
            for row_number, values in enumerate(reader, start=1):
                if TabularLayout.nonempty_row(values):
                    yield row_number, tuple(
                        None if value == "" else value for value in values
                    )
        finally:
            file_handle.close()
