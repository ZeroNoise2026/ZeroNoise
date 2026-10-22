"""Plain-text parser with encoding detection."""

from __future__ import annotations

from typing import Any

import chardet

from ..schemas import FilePreview
from .base import Parser

_PREVIEW_CHARS = 12_000
_SNIFF_BYTES = 64 * 1024


class TextParser(Parser):
    kind = "text"

    def _decode(self, data: bytes) -> tuple[str, str]:
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            pass
        guess = chardet.detect(data[:_SNIFF_BYTES]) or {}
        encoding = guess.get("encoding") or "latin-1"
        return data.decode(encoding, errors="replace"), encoding

    def meta(self, data: bytes, *, filename: str) -> dict[str, Any]:
        text, encoding = self._decode(data)
        return {
            "kind": self.kind,
            "chars": len(text),
            "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            "encoding": encoding,
        }

    def preview(self, data: bytes, *, filename: str) -> FilePreview:
        text, _ = self._decode(data)
        truncated = len(text) > _PREVIEW_CHARS
        if truncated:
            text = text[:_PREVIEW_CHARS] + "\n\n…(truncated)"
        return FilePreview(kind=self.kind, text=text, truncated=truncated)
