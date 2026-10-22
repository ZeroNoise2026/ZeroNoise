"""
main.py
zeroNoise API — the one HTTP service behind the web app: auth, user data,
briefings, skills and the RAG question flow (question/).

Endpoints:
  GET  /api/tickers              — available tickers for watchlist
  GET  /api/watchlist             — user's watchlist
  POST /api/watchlist             — add ticker
  DELETE /api/watchlist/{ticker}  — remove ticker
  GET  /api/preferences           — user preferences (timezone, etc.)
  PUT  /api/preferences           — update preferences
  GET  /api/briefings             — recent daily briefings
  GET  /api/briefings/latest      — most recent briefing
  POST /api/chat/stream           — chat: skill routing, then RAG (SSE)
  POST /api/summarize             — on-demand ticker summary

Run:
  uvicorn main:app --port 8000 --reload
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config  # noqa: F401  (loads .env before anything reads the environment)
import db
from auth import get_current_user
from files.routes import router as files_router
from question.models import AskRequest
from question.sse import ask_sse
from research.context import fetch_context
from research.briefing import build_briefing
from summarizer import generate_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Probe the skills package once, at import time.
#
# A missing package is a DEPLOYMENT error, and it needs to be loud here. It
# used to be swallowed by the per-request try/except below, which meant a
# container image built without the package disabled skill routing silently
# — every chat request quietly fell through to RAG with only a per-request
# warning. Genuine runtime routing failures are still handled per-request.
try:
    from skills import get_skill as _get_skill
    from skills.router import route as _route_skill
    _SKILLS_AVAILABLE = True
except ImportError as _skills_import_err:
    _get_skill = None
    _route_skill = None
    _SKILLS_AVAILABLE = False
    logging.error(
        "skills package unavailable — skill routing is DISABLED and all chat "
        "will fall back to RAG. For local dev run `./dev.sh setup api`; "
        "for the container make sure the Dockerfile installs libs/skills. Cause: %s",
        _skills_import_err,
    )
else:
    logging.info("skills package loaded — skill routing enabled")


app = FastAPI(title="zeroNoise API", version="1.0.0")

# CORS: in prod set ALLOWED_ORIGINS="https://your-app.vercel.app,https://your-app-*.vercel.app"
# Defaults to localhost for dev.
_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
_allowed_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=os.getenv("ALLOWED_ORIGIN_REGEX"),  # e.g. https://.*\.vercel\.app
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Feature routers
app.include_router(files_router)


# ── Tickers ───────────────────────────────────────────────────

@app.get("/api/tickers")
def list_tickers():
    return db.get_tracked_tickers()


# ── Watchlist ─────────────────────────────────────────────────

class WatchlistAdd(BaseModel):
    ticker: str

@app.get("/api/watchlist")
def get_watchlist(user_id: str = Depends(get_current_user)):
    return db.get_watchlist(user_id)

@app.post("/api/watchlist")
def add_watchlist(body: WatchlistAdd, user_id: str = Depends(get_current_user)):
    ticker = body.ticker.upper().strip()
    db.add_to_watchlist(user_id, ticker)
    return {"status": "ok", "ticker": ticker}

@app.delete("/api/watchlist/{ticker}")
def remove_watchlist(ticker: str, user_id: str = Depends(get_current_user)):
    db.remove_from_watchlist(user_id, ticker.upper())
    return {"status": "ok"}


# ── Preferences ───────────────────────────────────────────────

class PreferencesUpdate(BaseModel):
    timezone: str = "America/New_York"
    briefing_enabled: bool = True

@app.get("/api/preferences")
def get_preferences(user_id: str = Depends(get_current_user)):
    try:
        prefs = db.get_preferences(user_id)
    except Exception:
        prefs = None
    if not prefs:
        return {"user_id": user_id, "timezone": "America/New_York", "briefing_enabled": True}
    return prefs

@app.put("/api/preferences")
def update_preferences(body: PreferencesUpdate, user_id: str = Depends(get_current_user)):
    try:
        db.upsert_preferences(user_id, body.timezone, body.briefing_enabled)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Preferences table not available: {e}")
    return {"status": "ok"}


# ── Briefings ─────────────────────────────────────────────────

@app.get("/api/briefings")
def list_briefings(limit: int = 7, user_id: str = Depends(get_current_user)):
    try:
        return db.get_briefings(user_id, limit=limit)
    except Exception:
        return []

@app.get("/api/briefings/latest")
def latest_briefing(user_id: str = Depends(get_current_user)):
    try:
        briefing = db.get_latest_briefing(user_id)
    except Exception:
        briefing = None
    if not briefing:
        return {"message": "No briefings yet. Your first briefing will be generated at 8:00 AM your local time."}
    return briefing


@app.get("/api/briefings/by-date")
def briefing_by_date(date: str, user_id: str = Depends(get_current_user)):
    """Fetch a briefing for the given YYYY-MM-DD. 404 if no briefing that day."""
    try:
        briefing = db.get_briefing_by_date(user_id, date)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not briefing:
        raise HTTPException(status_code=404, detail=f"No briefing for {date}")
    return briefing


@app.get("/api/briefings/dates")
def briefing_dates(date_from: str, date_to: str, user_id: str = Depends(get_current_user)):
    """Return list of dates with briefings within [date_from, date_to].

    Frontend uses this to grey out days without data in the date picker.
    Both bounds are inclusive, format YYYY-MM-DD.
    """
    try:
        dates = db.get_briefing_dates(user_id, date_from, date_to)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"dates": dates}


# ── Refresh Briefing (on-demand) ─────────────────────────────

@app.post("/api/briefings/refresh")
def refresh_briefing(user_id: str = Depends(get_current_user)):
    """Generate a new briefing right now for the user's watchlist."""
    watchlist = db.get_watchlist(user_id)
    tickers = [w["ticker"] for w in watchlist]
    if not tickers:
        raise HTTPException(status_code=400, detail="Watchlist is empty. Add tickers first.")

    local_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if build_briefing(user_id, tickers, local_date) is None:
        raise HTTPException(status_code=404, detail="No data available for any watchlist ticker.")
    return {"status": "ok", "briefing_date": local_date}


# ── RAG Chat ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    ticker: Optional[str] = None             # explicit force-bind (e.g. clarification chip click)
    context_tickers: Optional[list[str]] = None  # watchlist fallback scope
    session_id: Optional[str] = None         # if absent, a new session is created

@app.post("/api/chat/stream")
def chat_stream(body: ChatRequest, user_id: str = Depends(get_current_user)):
    """SSE streaming chat — skill routing first, then the RAG question flow.

    Persists the user message up-front and the assistant message (with its
    citations) once the stream completes.
    """
    # Resolve or create the session.
    session_id = body.session_id
    is_new_session = False
    if session_id and not db.get_chat_session(user_id, session_id):
        # Stale id (another device deleted it) — create a new one.
        session_id = None
    if not session_id:
        session_id = db.create_chat_session(user_id, title=body.question)["id"]
        is_new_session = True

    # Persist the user message up-front so it survives a stream error.
    tickers_hint = [body.ticker.upper()] if body.ticker else []
    try:
        db.append_chat_message(session_id, "user", body.question, tickers=tickers_hint)
    except Exception as e:
        logging.warning(f"Failed to persist user message: {e}")

    def persist_assistant(text: str, sources: Optional[list[dict]] = None) -> None:
        try:
            db.append_chat_message(session_id, "assistant", text, tickers=tickers_hint, sources=sources)
        except Exception as e:
            logging.warning(f"Failed to persist assistant message: {e}")

    def event(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    async def stream():
        # Announce session id so the frontend can update its URL/state.
        yield event({"type": "session", "id": session_id, "is_new": is_new_session})

        # ── Skill routing ─────────────────────────────────────
        # A matching skill (generate_report, compare_tickers, ...) answers
        # instead of RAG; its output is emitted as token events so the
        # frontend renders it like any answer. Any router or skill failure
        # falls through to RAG. Skills are synchronous, so they run in a
        # worker thread to keep the event loop free.
        decision = None
        if _SKILLS_AVAILABLE:
            try:
                decision = await asyncio.to_thread(_route_skill, body.question)
            except Exception as e:
                logging.warning("Skill router failed; falling back to RAG: %s", e)

        if decision is not None and decision.skill:
            logging.info("Routing to skill %r with args=%s", decision.skill, decision.args)
            try:
                result = await asyncio.to_thread(_get_skill(decision.skill).run, **decision.args)
                text = result.content or ""
                if result.artifacts:
                    # Tell the user where the saved report is.
                    text += "\n\n_Saved: " + ", ".join(str(p) for p in result.artifacts) + "_"
                yield event({"type": "token", "text": text})
                yield "data: [DONE]\n\n"
                await asyncio.to_thread(persist_assistant, text)
                return
            except Exception as e:
                logging.exception("Skill %r raised: %s", decision.skill, e)

        # ── RAG question flow ─────────────────────────────────
        ask = AskRequest(
            query=body.question,
            tickers=[body.ticker] if body.ticker else [],
            context_tickers=[t.upper() for t in (body.context_tickers or []) if t],
        )
        answer: list[str] = []
        sources: Optional[list[dict]] = None
        async for line in ask_sse(ask):
            yield line
            # Accumulate the answer and citations for persistence.
            if not line.startswith("data: {"):
                continue
            obj = json.loads(line[6:])
            if obj.get("type") == "token":
                answer.append(obj.get("text") or "")
            elif obj.get("type") == "sources" and isinstance(obj.get("items"), list):
                sources = obj["items"]

        final_text = "".join(answer).strip()
        if final_text:
            await asyncio.to_thread(persist_assistant, final_text, sources)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Chat sessions ─────────────────────────────────────────────

@app.get("/api/chat/sessions")
def list_sessions(user_id: str = Depends(get_current_user)):
    try:
        return db.list_chat_sessions(user_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/chat/sessions/{session_id}/messages")
def get_session_messages(session_id: str, user_id: str = Depends(get_current_user)):
    # Ownership check
    session = db.get_chat_session(user_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session": session,
        "messages": db.list_chat_messages(session_id),
    }


@app.delete("/api/chat/sessions/{session_id}")
def remove_session(session_id: str, user_id: str = Depends(get_current_user)):
    db.delete_chat_session(user_id, session_id)
    return {"status": "ok"}


@app.delete("/api/chat/sessions")
def remove_all_sessions(user_id: str = Depends(get_current_user)):
    count = db.delete_all_chat_sessions(user_id)
    return {"status": "ok", "deleted": count}


# ── On-demand Summarization ───────────────────────────────────

class SummarizeRequest(BaseModel):
    ticker: str

@app.post("/api/summarize")
def summarize(body: SummarizeRequest, user_id: str = Depends(get_current_user)):
    ticker = body.ticker.upper().strip()
    ctx = fetch_context(ticker, news_limit=100)
    if ctx.total_chars == 0:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")
    report = generate_summary(ctx)
    return {"ticker": ticker, "report": report}


# ── Health ────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}
