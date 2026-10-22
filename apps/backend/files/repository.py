"""CRUD for the `user_files` table. Thin wrapper over the supabase client."""

from __future__ import annotations

import logging
from typing import Any, Optional

from db import get_client
from .constants import MAX_FILES_PER_USER

logger = logging.getLogger(__name__)

_TABLE = "user_files"
_FIELDS = (
    "id, user_id, session_id, filename, mime_type, size_bytes, "
    "storage_path, parsed_meta, created_at, created_by"
)


def count_for_user(user_id: str) -> int:
    res = (
        get_client().table(_TABLE)
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    return res.count or 0


def assert_under_quota(user_id: str) -> None:
    if count_for_user(user_id) >= MAX_FILES_PER_USER:
        raise QuotaExceeded(
            f"Per-user file limit reached ({MAX_FILES_PER_USER}). Delete some files first."
        )


def insert(
    *,
    user_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    storage_path: str,
    parsed_meta: Optional[dict[str, Any]] = None,
    session_id: Optional[str] = None,
    created_by: str = "user",
) -> dict:
    row = {
        "user_id": user_id,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "storage_path": storage_path,
        "parsed_meta": parsed_meta,
        "session_id": session_id,
        "created_by": created_by,
    }
    res = get_client().table(_TABLE).insert(row).execute()
    return res.data[0]


def list_for_user(
    user_id: str,
    *,
    session_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> list[dict]:
    q = (
        get_client().table(_TABLE)
        .select(_FIELDS)
        .eq("user_id", user_id)
        .order("created_at", desc=True)
    )
    if session_id is not None:
        q = q.eq("session_id", session_id)
    if created_by is not None:
        q = q.eq("created_by", created_by)
    return q.execute().data


def get(user_id: str, file_id: str) -> Optional[dict]:
    res = (
        get_client().table(_TABLE)
        .select(_FIELDS)
        .eq("user_id", user_id)
        .eq("id", file_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def update_session(user_id: str, file_id: str, session_id: Optional[str]) -> Optional[dict]:
    res = (
        get_client().table(_TABLE)
        .update({"session_id": session_id})
        .eq("user_id", user_id)
        .eq("id", file_id)
        .execute()
    )
    return res.data[0] if res.data else None


def delete(user_id: str, file_id: str) -> bool:
    res = (
        get_client().table(_TABLE)
        .delete()
        .eq("user_id", user_id)
        .eq("id", file_id)
        .execute()
    )
    return bool(res.data)


class QuotaExceeded(Exception):
    """Raised when the per-user file count cap is hit."""
