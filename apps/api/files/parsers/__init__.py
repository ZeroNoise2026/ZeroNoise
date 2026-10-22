"""Parser interface + dispatcher.

Each parser turns raw bytes (already in memory) into:
  - `meta`: a small JSON-serialisable dict stored on the row at upload time
  - `preview`: a richer FilePreview returned by GET /api/files/{id}/preview

Adding a new file type means: write a Parser, register it in the dispatcher.
"""

from __future__ import annotations

from typing import Optional

from ..constants import kind_for_mime
from .base import Parser, ParseError
from .csv_parser import CsvParser
from .docx_parser import DocxParser
from .excel_parser import ExcelParser
from .pdf_parser import PdfParser
from .text_parser import TextParser

_REGISTRY: dict[str, Parser] = {
    "excel": ExcelParser(),
    "csv": CsvParser(),
    "pdf": PdfParser(),
    "docx": DocxParser(),
    "text": TextParser(),
}


def get_parser_for_mime(mime: str) -> Optional[Parser]:
    kind = kind_for_mime(mime)
    return _REGISTRY.get(kind) if kind else None


__all__ = ["Parser", "ParseError", "get_parser_for_mime"]
