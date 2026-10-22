"""User-file upload, preview, and storage management.

Public surface:
    files.routes.router    — FastAPI router, mount on the main app.

Internal layout:
    constants    — size/type limits
    schemas      — Pydantic request/response models
    storage      — Supabase Storage wrapper (upload/delete/sign URL)
    repository   — user_files table CRUD (named to avoid shadowing top-level db)
    parsers/     — per-mime parsers producing previews
    routes       — HTTP layer that ties the above together
"""
