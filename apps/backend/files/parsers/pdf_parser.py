"""PDF parser using pypdf for text extraction."""

from __future__ import annotations

import io
from typing import Any

from pypdf import PdfReader

from ..schemas import FilePreview
from .base import ParseError, Parser

_PREVIEW_PAGES = 5
_PREVIEW_CHARS = 12_000


class PdfParser(Parser):
    kind = "pdf"

    def _open(self, data: bytes) -> PdfReader:
        try:
            return PdfReader(io.BytesIO(data))
        except Exception as e:
            raise ParseError(f"Failed to read PDF: {e}") from e

    def meta(self, data: bytes, *, filename: str) -> dict[str, Any]:
        reader = self._open(data)
        try:
            page_count = len(reader.pages)
        except Exception:
            page_count = 0
        # Probe the first page to know whether the PDF actually has selectable text.
        has_text = False
        if page_count:
            try:
                has_text = bool((reader.pages[0].extract_text() or "").strip())
            except Exception:
                has_text = False
        return {"kind": self.kind, "page_count": page_count, "has_text": has_text}

    def preview(self, data: bytes, *, filename: str) -> FilePreview:
        reader = self._open(data)
        out: list[str] = []
        total_pages = len(reader.pages)
        pages_to_read = min(_PREVIEW_PAGES, total_pages)
        for i in range(pages_to_read):
            try:
                txt = reader.pages[i].extract_text() or ""
            except Exception:
                txt = ""
            if txt:
                out.append(f"── Page {i + 1} ──\n{txt}")
        text = "\n\n".join(out).strip()
        truncated = total_pages > pages_to_read or len(text) > _PREVIEW_CHARS
        if len(text) > _PREVIEW_CHARS:
            text = text[:_PREVIEW_CHARS] + "\n\n…(truncated)"
        if not text:
            return FilePreview(
                kind=self.kind,
                error="No selectable text found (this may be a scanned PDF; OCR is not enabled).",
                truncated=False,
            )
        return FilePreview(kind=self.kind, text=text, truncated=truncated)
