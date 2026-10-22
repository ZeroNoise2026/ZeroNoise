"""Supabase Storage wrapper.

Encapsulates the bucket layout and signed-URL conventions in one place so that
routes and tests don't sprinkle storage paths around.

Path convention:
    <user_id>/<file_id>.<ext>

The user_id prefix lets us write straightforward Storage RLS policies in the
Supabase dashboard:

    bucket_id = 'user-files'
    AND (storage.foldername(name))[1] = auth.uid()::text
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from db import get_client  # reuse the singleton supabase client from main app
from .constants import SIGNED_URL_TTL_SECONDS, STORAGE_BUCKET

logger = logging.getLogger(__name__)


def _ext_from_filename(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return ext.lower() if dot else ""


def build_path(user_id: str, file_id: str, filename: str) -> str:
    ext = _ext_from_filename(filename)
    return f"{user_id}/{file_id}.{ext}" if ext else f"{user_id}/{file_id}"


def upload(path: str, data: bytes, mime_type: str) -> None:
    """Upload bytes to the bucket. Raises on failure."""
    res = get_client().storage.from_(STORAGE_BUCKET).upload(
        path=path,
        file=data,
        file_options={
            "content-type": mime_type,
            "upsert": "false",
        },
    )
    # supabase-py returns an UploadResponse; non-2xx raises in newer versions,
    # but defensively check for an error attribute too.
    err = getattr(res, "error", None)
    if err:
        raise RuntimeError(f"Storage upload failed: {err}")


def download(path: str) -> bytes:
    """Fetch raw bytes. Used server-side by parsers."""
    return get_client().storage.from_(STORAGE_BUCKET).download(path)


def delete(path: str) -> None:
    """Best-effort delete; logs but does not raise so callers can still drop the row."""
    try:
        get_client().storage.from_(STORAGE_BUCKET).remove([path])
    except Exception as e:
        logger.warning("Storage delete failed for %s: %s", path, e)


def signed_url(path: str, expires_in: int = SIGNED_URL_TTL_SECONDS) -> Optional[str]:
    """Return a temporary public URL the client can use to download/preview.

    Returns None if signing fails so callers can fall back to a 503.
    """
    try:
        res = get_client().storage.from_(STORAGE_BUCKET).create_signed_url(
            path=path, expires_in=expires_in
        )
        return res.get("signedURL") or res.get("signed_url")
    except Exception as e:
        logger.warning("Signed URL failed for %s: %s", path, e)
        return None
