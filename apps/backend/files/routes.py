"""HTTP routes for file upload, listing, preview, and deletion.

Conventions:
    - All endpoints require auth via `get_current_user`.
    - Storage path is owned by the user (`<user_id>/<file_id>.<ext>`); RLS in
      Postgres + Storage policies enforce ownership defence-in-depth.
    - Parser failures are non-fatal at upload time: the row is still created
      with `parsed_meta = {kind, error}` so the user can delete it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from auth import get_current_user

from . import repository, storage
from .constants import ACCEPTED_MIME_TYPES, MAX_FILE_SIZE_BYTES, kind_for_mime
from .parsers import ParseError, get_parser_for_mime
from .schemas import AttachToSession, FilePreview, FileRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])


# ── Upload ──────────────────────────────────────────────────────

@router.post("", response_model=FileRecord, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    user_id: str = Depends(get_current_user),
) -> FileRecord:
    mime = (file.content_type or "").split(";", 1)[0].strip().lower()
    if mime not in ACCEPTED_MIME_TYPES:
        raise HTTPException(415, f"Unsupported file type: {mime or 'unknown'}")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            413,
            f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB limit "
            f"(got {len(data) / 1024 / 1024:.1f} MB).",
        )

    try:
        repository.assert_under_quota(user_id)
    except repository.QuotaExceeded as e:
        raise HTTPException(409, str(e)) from None

    file_id = str(uuid.uuid4())
    path = storage.build_path(user_id, file_id, file.filename or "file")

    # Best-effort parse for the meta blob; never block upload on parse failure.
    parsed_meta: dict
    parser = get_parser_for_mime(mime)
    if parser is None:
        parsed_meta = {"kind": kind_for_mime(mime) or "unknown"}
    else:
        try:
            parsed_meta = parser.meta(data, filename=file.filename or "")
        except ParseError as e:
            parsed_meta = {"kind": parser.kind, "error": str(e)}
        except Exception as e:  # defensive — unknown parser bug
            logger.exception("Parser meta crashed for %s", file.filename)
            parsed_meta = {"kind": parser.kind, "error": f"parse failed: {e}"}

    try:
        storage.upload(path, data, mime)
    except Exception as e:
        logger.exception("Storage upload failed")
        raise HTTPException(503, f"Storage upload failed: {e}") from e

    try:
        row = repository.insert(
            user_id=user_id,
            filename=file.filename or "file",
            mime_type=mime,
            size_bytes=len(data),
            storage_path=path,
            parsed_meta=parsed_meta,
            session_id=session_id,
        )
    except Exception as e:
        # DB write failed → roll back the storage object so we don't leak.
        storage.delete(path)
        logger.exception("DB insert failed; rolled back storage object")
        raise HTTPException(503, f"Database insert failed: {e}") from e

    return FileRecord(**row)


# ── List / Get ──────────────────────────────────────────────────

@router.get("", response_model=list[FileRecord])
def list_files(
    session_id: Optional[str] = None,
    created_by: Optional[str] = None,
    user_id: str = Depends(get_current_user),
) -> list[FileRecord]:
    rows = repository.list_for_user(
        user_id, session_id=session_id, created_by=created_by
    )
    return [FileRecord(**r) for r in rows]


@router.get("/{file_id}", response_model=FileRecord)
def get_file(file_id: str, user_id: str = Depends(get_current_user)) -> FileRecord:
    row = repository.get(user_id, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    return FileRecord(**row)


# ── Preview ─────────────────────────────────────────────────────

@router.get("/{file_id}/preview", response_model=FilePreview)
def preview_file(file_id: str, user_id: str = Depends(get_current_user)) -> FilePreview:
    row = repository.get(user_id, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    parser = get_parser_for_mime(row["mime_type"])
    if parser is None:
        raise HTTPException(415, f"No preview available for {row['mime_type']}")

    try:
        data = storage.download(row["storage_path"])
    except Exception as e:
        logger.exception("Storage download failed")
        raise HTTPException(503, f"Could not fetch file: {e}") from e

    try:
        return parser.preview(data, filename=row["filename"])
    except ParseError as e:
        return FilePreview(kind=parser.kind, error=str(e))
    except Exception as e:
        logger.exception("Parser crashed")
        return FilePreview(kind=parser.kind, error=f"preview failed: {e}")


# ── Download (signed URL redirect) ──────────────────────────────

@router.get("/{file_id}/download")
def download_file(file_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """Return a short-lived signed URL the client can use directly.

    The frontend GETs this and then either follows the URL (anchor download)
    or fetches it for client-side rendering (e.g. PDF.js).
    """
    row = repository.get(user_id, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    url = storage.signed_url(row["storage_path"])
    if not url:
        raise HTTPException(503, "Could not sign download URL")
    return {"url": url, "filename": row["filename"], "mime_type": row["mime_type"]}


# ── Attach to session ───────────────────────────────────────────

@router.post("/{file_id}/attach", response_model=FileRecord)
def attach_to_session(
    file_id: str,
    body: AttachToSession,
    user_id: str = Depends(get_current_user),
) -> FileRecord:
    row = repository.update_session(user_id, file_id, body.session_id)
    if not row:
        raise HTTPException(404, "File not found")
    return FileRecord(**row)


# ── Delete ──────────────────────────────────────────────────────

@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: str, user_id: str = Depends(get_current_user)) -> None:
    row = repository.get(user_id, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    # Drop storage first; if it fails we still want to drop the row so the user
    # can stop seeing the file. Storage delete is best-effort by design.
    storage.delete(row["storage_path"])
    if not repository.delete(user_id, file_id):
        raise HTTPException(503, "Failed to delete row")
