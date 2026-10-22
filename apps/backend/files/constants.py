"""File-upload limits and accepted MIME types.

Keep this small and dependency-free so other modules (and tests) can import it
without pulling in supabase / pandas / etc.
"""

from __future__ import annotations

from typing import Optional

# 20 MB. Cloud Run's default request limit is 32 MB; leave headroom for
# multipart overhead and other form fields.
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024

# Per-user file count cap. Prevents Storage abuse on the free tier.
MAX_FILES_PER_USER = 100

# Storage bucket name. Must exist in Supabase (Storage → New bucket → private).
STORAGE_BUCKET = "user-files"

# Signed-URL TTL for downloads / previews (seconds).
SIGNED_URL_TTL_SECONDS = 300

# MIME → canonical kind. The kind is what parsers and frontend dispatch on.
MIME_TO_KIND: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "text/csv": "csv",
    "application/csv": "csv",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "text",
    "text/markdown": "text",
}

ACCEPTED_MIME_TYPES = frozenset(MIME_TO_KIND)


def kind_for_mime(mime: str) -> Optional[str]:
    """Return canonical kind ('excel'|'csv'|'pdf'|'docx'|'text') or None if unsupported."""
    return MIME_TO_KIND.get(mime.split(";", 1)[0].strip().lower())
