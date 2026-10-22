"""
main.py
FastAPI backend for the ChatbotUI.

Endpoints:
  GET  /api/tickers              — available tickers for watchlist
  GET  /api/watchlist             — user's watchlist
  POST /api/watchlist             — add ticker
  DELETE /api/watchlist/{ticker}  — remove ticker
  GET  /api/preferences           — user preferences (timezone, etc.)
  PUT  /api/preferences           — update preferences
  GET  /api/briefings             — recent daily briefings
  GET  /api/briefings/latest      — most recent briefing
  POST /api/chat/stream           — RAG-based Q&A (SSE streaming)
  POST /api/summarize             — on-demand ticker summary

Run:
  uvicorn main:app --port 8000 --reload
"""

import logging
import os
import sys
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi import Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

# Make the shared `skills` package importable.
#
# The package lives at <project-root>/<clone>/skills/, where <clone> is
# whatever `git clone .../Skills.git` produced — "Skills" by default, which
# does NOT match `import skills` on a case-sensitive filesystem (Linux,
# Docker, CI). So probe the candidate clone names instead of assuming one.
#
# If no candidate matches, the package was pip-installed instead (see
# Skills/pyproject.toml) and the installed distribution is used — that is
# how the container image gets it.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _clone_name in ("Skills", "skills"):
    _skills_repo = _PROJECT_ROOT / _clone_name
    if (_skills_repo / "skills" / "__init__.py").is_file():
        if str(_skills_repo) not in sys.path:
            sys.path.insert(0, str(_skills_repo))
        break

import db
import rag
from auth import get_current_user
from fetcher import fetch_context
from files.routes import router as files_router
from summarizer import generate_summary, generate_briefing

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
        "will fall back to RAG. For local dev run `pip install -e ../../Skills`; "
        "for the container make sure requirements.txt installs it. Cause: %s",
        _skills_import_err,
    )
else:
    logging.info("skills package loaded — skill routing enabled")


app = FastAPI(title="QuantAgent Chat API", version="1.0.0")

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

    from datetime import datetime, timezone as tz
    local_date = datetime.now(tz.utc).strftime("%Y-%m-%d")

    contexts = []
    for ticker in tickers:
        try:
            ctx = fetch_context(ticker, news_limit=50)
            if ctx.total_chars > 0:
                contexts.append(ctx)
        except Exception as e:
            logging.warning(f"Failed to fetch {ticker}: {e}")

    if not contexts:
        raise HTTPException(status_code=404, detail="No data available for any watchlist ticker.")

    content = generate_briefing(contexts)
    header = f"# Daily Briefing — {local_date}\n\n> Tickers: {', '.join(tickers)}\n\n"
    db.upsert_briefing(user_id, local_date, header + content, tickers)
    return {"status": "ok", "briefing_date": local_date}


# ── RAG Chat ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    ticker: Optional[str] = None             # explicit force-bind (e.g. clarification chip click)
    context_tickers: Optional[list[str]] = None  # watchlist fallback scope
    session_id: Optional[str] = None         # if absent, a new session is created

@app.post("/api/chat/stream")
def chat_stream(body: ChatRequest, user_id: str = Depends(get_current_user)):
    """SSE streaming chat — proxies token stream from question-service, persists messages."""
    # Resolve or create the session.
    session_id = body.session_id
    is_new_session = False
    if session_id:
        existing = db.get_chat_session(user_id, session_id)
        if not existing:
            # Stale id (another device deleted it) — create a new one.
            session_id = None
    if not session_id:
        session = db.create_chat_session(user_id, title=body.question)
        session_id = session["id"]
        is_new_session = True

    # Persist the user message up-front so it survives a stream error.
    tickers_hint: list[str] = []
    if body.ticker:
        tickers_hint.append(body.ticker.upper())
    try:
        db.append_chat_message(session_id, "user", body.question, tickers=tickers_hint)
    except Exception as e:
        logging.warning(f"Failed to persist user message: {e}")

    import json as _json

    def proxy_and_persist():
        # Announce session id so frontend can update its URL/state.
        yield f"data: {_json.dumps({'type': 'session', 'id': session_id, 'is_new': is_new_session})}\n\n"

        # ── Skill routing ─────────────────────────────────────
        # Before falling into the generic RAG flow, check whether the user
        # message matches a registered skill (generate_report, compare_tickers,
        # ...). On a match we emit the skill's output as token events so the
        # frontend renders it the same way it renders a RAG answer.
        #
        # Any failure here (no API key, network blip, JSON parse error) lands
        # in `skill = None` and the request falls through to RAG below.
        decision = None
        if _SKILLS_AVAILABLE:
            try:
                decision = _route_skill(body.question)
            except Exception as e:
                logging.warning("Skill router failed; falling back to RAG: %s", e)
                decision = None

        if decision is not None and decision.skill:
            logging.info("Routing to skill %r with args=%s",
                         decision.skill, decision.args)
            try:
                result = _get_skill(decision.skill).run(**decision.args)
                content = result.content or ""
                # Emit as a single token chunk — the frontend accumulator
                # joins all token texts and treats it as the assistant
                # message verbatim.
                yield f"data: {_json.dumps({'type': 'token', 'text': content})}\n\n"
                # Surface artifact paths to the user so they can find the
                # saved report. Keep it short.
                if result.artifacts:
                    note = "\n\n_Saved: " + ", ".join(
                        str(p) for p in result.artifacts
                    ) + "_"
                    yield f"data: {_json.dumps({'type': 'token', 'text': note})}\n\n"
                yield "data: [DONE]\n\n"

                # Persist the assistant message before returning.
                final_text = content + (note if result.artifacts else "")
                try:
                    db.append_chat_message(
                        session_id, "assistant", final_text,
                        tickers=tickers_hint,
                    )
                except Exception as e:
                    logging.warning(f"Failed to persist assistant message: {e}")
                return
            except Exception as e:
                # Skill blew up — log it, then fall through to RAG so the
                # user still gets *something* useful.
                logging.exception("Skill %r raised: %s", decision.skill, e)

        assistant_buf: list[str] = []
        # Citation payload from the upstream `sources` SSE event. We capture
        # it here so it can be persisted alongside the assistant message;
        # the event itself is also forwarded to the browser for live render.
        sources_buf: Optional[list[dict]] = None
        for raw_line in rag.stream_answer_sse(
            body.question,
            ticker=body.ticker,
            context_tickers=body.context_tickers or [],
        ):
            yield raw_line
            # Parse to accumulate assistant tokens (best-effort; never block on error).
            for line in raw_line.splitlines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]" or payload.startswith("[Error:"):
                    continue
                try:
                    obj = _json.loads(payload)
                except Exception:
                    continue
                if obj.get("type") == "token":
                    assistant_buf.append(obj.get("text") or "")
                elif obj.get("type") == "sources":
                    items = obj.get("items")
                    if isinstance(items, list):
                        sources_buf = items

        # Persist assistant message after the stream completes.
        final_text = "".join(assistant_buf).strip()
        if final_text:
            try:
                db.append_chat_message(
                    session_id, "assistant", final_text,
                    tickers=tickers_hint,
                    sources=sources_buf,
                )
            except Exception as e:
                logging.warning(f"Failed to persist assistant message: {e}")

    return StreamingResponse(
        proxy_and_persist(),
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
    ctx = fetch_context(ticker)
    if ctx.total_chars == 0:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")
    report = generate_summary(ctx)
    return {"ticker": ticker, "report": report}


# ── Health ────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}
