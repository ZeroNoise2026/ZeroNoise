"""Pydantic models for the files API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class FileRecord(BaseModel):
    """Row in `user_files` returned to the client."""
    id: str
    user_id: str
    session_id: Optional[str] = None
    filename: str
    mime_type: str
    size_bytes: int
    parsed_meta: Optional[dict[str, Any]] = None
    created_at: datetime
    created_by: str = "user"  # 'user' | 'assistant'


class FilePreview(BaseModel):
    """Structured preview returned by GET /api/files/{id}/preview.

    Exactly one of `table` / `text` will be populated, depending on `kind`.
    """
    kind: str  # 'excel' | 'csv' | 'pdf' | 'docx' | 'text'
    table: Optional["TablePreview"] = None
    text: Optional[str] = None
    truncated: bool = False
    error: Optional[str] = None


class TablePreview(BaseModel):
    sheet_name: Optional[str] = None  # excel only
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int
    total_cols: int


class AttachToSession(BaseModel):
    session_id: Optional[str] = Field(
        None, description="UUID of a chat session to attach to. null to detach."
    )


FilePreview.model_rebuild()
