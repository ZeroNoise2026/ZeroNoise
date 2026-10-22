"""CSV parser using the stdlib `csv` module + chardet for encoding detection."""

from __future__ import annotations

import csv
import io
from typing import Any

import chardet

from ..schemas import FilePreview, TablePreview
from .base import ParseError, Parser

_PREVIEW_ROWS = 100
_PREVIEW_COLS = 50
_SNIFF_BYTES = 64 * 1024


class CsvParser(Parser):
    kind = "csv"

    def _decode(self, data: bytes) -> str:
        # Try UTF-8 first (the common case), fall back to chardet.
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        guess = chardet.detect(data[:_SNIFF_BYTES]) or {}
        encoding = guess.get("encoding") or "latin-1"
        try:
            return data.decode(encoding, errors="replace")
        except Exception as e:
            raise ParseError(f"Cannot decode CSV: {e}") from e

    def _read_rows(self, data: bytes) -> tuple[list[str], list[list[str]], int]:
        text = self._decode(data)
        sample = text[:_SNIFF_BYTES]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        try:
            header = next(reader)
        except StopIteration:
            return [], [], 0
        header = [c for c in header[:_PREVIEW_COLS]]

        rows: list[list[str]] = []
        total = 0
        for row in reader:
            total += 1
            if len(rows) < _PREVIEW_ROWS:
                rows.append([c for c in row[:_PREVIEW_COLS]])
        return header, rows, total

    def meta(self, data: bytes, *, filename: str) -> dict[str, Any]:
        header, _rows, total = self._read_rows(data)
        return {"kind": self.kind, "rows": total, "cols": len(header)}

    def preview(self, data: bytes, *, filename: str) -> FilePreview:
        header, rows, total = self._read_rows(data)
        truncated = total > _PREVIEW_ROWS or len(header) > _PREVIEW_COLS
        return FilePreview(
            kind=self.kind,
            truncated=truncated,
            table=TablePreview(
                columns=header,
                rows=rows,
                total_rows=total,
                total_cols=len(header),
            ),
        )
