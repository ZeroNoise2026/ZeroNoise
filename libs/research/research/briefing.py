"""
research/briefing.py
Daily watchlist briefings: generation (Claude) and the daily_briefings table.

Used by the API (POST /api/briefings/refresh) and the scheduled job
(apps/jobs/briefing.py). Model and effort come from LLM_MODEL / LLM_EFFORT.
"""

import logging
import os
from typing import Optional

from common import supabase
from common.llm import chat
from research.context import TickerContext, fetch_context

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM_PROMPT = """You are a senior financial analyst. Generate a concise daily briefing for the user's watchlist.

Requirements:
1. For each ticker, write a short paragraph (80-120 words) covering:
   - Current price & recent move
   - Key news (if any)
   - Any notable financial data
2. Use Markdown with ## headers for each ticker
3. Be factual — cite specific numbers and dates
4. End with a brief 2-sentence overall market outlook
5. If no meaningful data exists for a ticker, note it briefly and move on"""


def generate_briefing(contexts: list[TickerContext]) -> str:
    """Generate a multi-ticker daily briefing."""
    sections = ["Generate a daily investment briefing for the following watchlist tickers.\n"]
    for ctx in contexts:
        parts = [f"### {ctx.ticker}"]
        if ctx.price_text:
            parts.append(f"Price data:\n{ctx.price_text}")
        if ctx.earnings_text:
            parts.append(f"Earnings:\n{ctx.earnings_text}")
        if ctx.news_text:
            parts.append(f"Recent news:\n{ctx.news_text[:3000]}")
        if not ctx.price_text and not ctx.news_text:
            parts.append("(Limited data available)")
        sections.append("\n".join(parts))
    user_prompt = "\n\n---\n\n".join(sections)

    content, _usage = chat(
        messages=[
            {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=os.getenv("LLM_MODEL", "claude-haiku-5-5"),
        effort=os.getenv("LLM_EFFORT", "medium"),
        service="briefing",
    )
    return content


def build_briefing(user_id: str, tickers: list[str], briefing_date: str) -> Optional[str]:
    """Generate and save one user's briefing for `briefing_date`.

    Returns the saved Markdown, or None when no ticker has any data.
    """
    contexts = []
    for ticker in tickers:
        try:
            ctx = fetch_context(ticker, news_limit=50)
            if ctx.total_chars > 0:
                contexts.append(ctx)
        except Exception as e:
            logger.warning(f"Failed to fetch {ticker}: {e}")
    if not contexts:
        return None

    content = (
        f"# Daily Briefing — {briefing_date}\n\n> Tickers: {', '.join(tickers)}\n\n"
        + generate_briefing(contexts)
    )
    upsert_briefing(user_id, briefing_date, content, tickers)
    return content


# ── daily_briefings / user_preferences ──────────────────────

def get_users_needing_briefing() -> list[dict]:
    """Fetch users with briefing_enabled=true who have at least one watchlist entry."""
    client = supabase.get_client()
    prefs = (
        client.table("user_preferences")
        .select("user_id, timezone")
        .eq("briefing_enabled", True)
        .execute()
        .data
    )
    if not prefs:
        return []

    user_ids = [p["user_id"] for p in prefs]
    watchlist = (
        client.table("user_watchlist")
        .select("user_id, ticker")
        .in_("user_id", user_ids)
        .execute()
        .data
    )

    wl_by_user: dict[str, list[str]] = {}
    for row in watchlist:
        wl_by_user.setdefault(row["user_id"], []).append(row["ticker"])

    results = []
    for p in prefs:
        uid = p["user_id"]
        tickers = wl_by_user.get(uid, [])
        if tickers:
            results.append({
                "user_id": uid,
                "timezone": p["timezone"],
                "tickers": sorted(tickers),
            })
    return results


def briefing_exists(user_id: str, briefing_date: str) -> bool:
    client = supabase.get_client()
    resp = (
        client.table("daily_briefings")
        .select("user_id")
        .eq("user_id", user_id)
        .eq("briefing_date", briefing_date)
        .limit(1)
        .execute()
    )
    return len(resp.data) > 0


def upsert_briefing(user_id: str, briefing_date: str, content: str, tickers: list[str]) -> None:
    client = supabase.get_client()
    client.table("daily_briefings").upsert({
        "user_id": user_id,
        "briefing_date": briefing_date,
        "content": content,
        "tickers": tickers,
    }).execute()
