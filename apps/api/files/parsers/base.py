"""Parser ABC + shared error type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..schemas import FilePreview


class ParseError(Exception):
    """Raised by parsers when content is unreadable. Caller turns this into
    a graceful error stored in `parsed_meta` so the file row still exists."""


class Parser(ABC):
    """Stateless. Implementations MAY be heavy (open big workbooks); keep them
    cheap on init and do real work in `meta` / `preview`."""

    #: canonical kind, matches the dispatcher key
    kind: str

    @abstractmethod
    def meta(self, data: bytes, *, filename: str) -> dict[str, Any]:
        """Cheap summary recorded on the user_files row at upload time."""

    @abstractmethod
    def preview(self, data: bytes, *, filename: str) -> FilePreview:
        """Detailed preview returned to the frontend."""
