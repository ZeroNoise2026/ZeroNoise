"""
question/orchestrator.py
Main orchestrator: route -> tier check -> retrieve/fetch -> generate -> respond

Fast paths (skip LLM, <2s):
  - price_query: direct DB lookup + live quote → formatted response
"""
from __future__ import annotations

import asyncio
import logging
import queue
import time

from config import HOT_PROMOTION_THRESHOLD
from shared.db import is_tracked, is_ingested, promote_ticker
from shared.db import record_ticker_query, count_recent_ticker_queries
from shared.db import get_earnings, get_price_snapshots
from question.models import AskRequest, Intent, QueryMode, Source
from question.router import router
from question.retriever import retrieve
from question.live_fetcher import fetch_tier2_context, fetch_live_quote_raw
from question.tier2_cache import cache
from question.generator import generate_answer_stream


def _dedupe_sources(sources: list[Source]) -> list[dict]:
    """Drop duplicates by Source.id, preserving first-seen order. Returns dicts
    so the SSE layer can json.dumps() without further conversion."""
    seen: set[str] = set()
    out: list[dict] = []
    for s in sources:
        if s.id in seen:
            continue
        seen.add(s.id)
        out.append(s.model_dump())
    return out

logger = logging.getLogger(__name__)


def _maybe_promote(ticker: str) -> None:
    """Count a Tier 2 query; promote the ticker once it's hot (>=N queries in 24h).

    Counted in Postgres, not in-process, so all service instances share one tally.
    """
    record_ticker_query(ticker)
    if is_tracked(ticker):
        return  # already promoted, waiting for the pipeline to ingest it
    if count_recent_ticker_queries(ticker) >= HOT_PROMOTION_THRESHOLD:
        promote_ticker(ticker)


def _fmt_money(val) -> str:
    """Format a number as human-readable money (e.g. $24.9B, $840M, $1.2K)."""
    if val is None:
        return "N/A"
    v = abs(float(val))
    sign = "-" if float(val) < 0 else ""
    if v >= 1e12:
        return f"{sign}${v/1e12:.2f}T"
    if v >= 1e9:
        return f"{sign}${v/1e9:.1f}B"
    if v >= 1e6:
        return f"{sign}${v/1e6:.0f}M"
    if v >= 1e3:
        return f"{sign}${v/1e3:.0f}K"
    return f"{sign}${v:,.0f}"


# ═══════════════════════════════════════════
# Fast path: price_query — skip LLM, <2s
# ═══════════════════════════════════════════

async def _fast_price_response(tickers: list[str]) -> str:
    """Build a formatted price response directly from DB + live API.

    Template-rendered (Plan 4 Path A for PRICE_QUERY). No LLM involved — this
    intent is already deterministic. We keep the old behaviour as fallback
    in case the template renderer raises (which would indicate a bug, not
    bad LLM output, but we stay safe).
    """
    from question.schemas.price_query import TEMPLATE, build_context
    from question.templates import render, RenderError

    parts: list[str] = []
    for ticker in tickers:
        live = await fetch_live_quote_raw(ticker)
        earnings = get_earnings(ticker, limit=4)
        snapshots = get_price_snapshots(ticker, limit=1) if not live else []
        ctx = build_context(
            ticker=ticker, live=live, earnings=earnings, snapshots=snapshots,
        )
        try:
            parts.append(render(TEMPLATE, ctx))
        except RenderError as e:
            logger.error(f"PRICE_QUERY template render failed for {ticker}: {e}")
            # Minimal safe fallback
            price = (live or {}).get("price") if live else None
            parts.append(
                f"## {ticker}\n**Price:** {_fmt_money(price) if price else 'N/A'}\n"
                f"*(template error — showing minimal info)*"
            )
    return "\n\n".join(parts)


# ═══════════════════════════════════════════
# Path A (Plan 4): structured generation for EARNINGS / COMPARISON
# LLM produces JSON narrative, backend renders all numbers from DB.
# Failures raise _StructuredFallback so the orchestrator can route to Path B.
# ═══════════════════════════════════════════

class _StructuredFallback(RuntimeError):
    """Raised when structured generation fails — caller should fallback to legacy path."""


async def _structured_earnings_response(query: str, tickers: list[str], target_quarter: str | None = None) -> str:
    """Path A for EARNINGS_ANALYSIS. Raises _StructuredFallback on any failure."""
    from question.schemas import earnings_analysis as EA
    from question.templates import render, RenderError
    from question.structured import generate_json, StructuredGenError

    parts: list[str] = []
    for ticker in tickers:
        earnings = get_earnings(ticker, limit=8)
        if not earnings:
            raise _StructuredFallback(f"no earnings rows for {ticker}")

        context_summary = EA.summarize_for_llm(earnings, target_quarter=target_quarter)
        quarter_hint = f" The user asked specifically about {target_quarter}." if target_quarter else ""
        user_prompt = (
            f"User question: {query}\n\n"
            f"CONTEXT SUMMARY (verified facts for {ticker}, use qualitatively):\n"
            f"{context_summary}\n"
            f"{quarter_hint}\n\n"
            "Respond with ONLY a JSON object matching the required schema."
        )
        t0 = time.time()
        try:
            llm_output, usage = generate_json(
                system_prompt=EA.SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=EA.SCHEMA,
                few_shot=EA.FEW_SHOT,
                intent_label=f"earnings:{ticker}",
            )
        except StructuredGenError as e:
            raise _StructuredFallback(f"LLM structured gen failed: {e}")

        try:
            ctx = EA.build_context(
                ticker=ticker,
                earnings=earnings,
                llm_output=llm_output,
                target_quarter=target_quarter,
            )
            md = render(EA.TEMPLATE, ctx)
        except RenderError as e:
            raise _StructuredFallback(f"render failed: {e}")

        logger.info(
            f"structured_gen: intent=earnings ticker={ticker} "
            f"tokens={usage.get('total_tokens')} latency={int((time.time()-t0)*1000)}ms render_ok=True"
        )
        parts.append(md)
    return "\n\n---\n\n".join(parts)


async def _structured_comparison_response(query: str, tickers: list[str], target_quarter: str | None = None) -> str:
    """Path A for COMPARISON. Raises _StructuredFallback on any failure."""
    from question.schemas import comparison as CMP
    from question.templates import render, RenderError
    from question.structured import generate_json, StructuredGenError

    per_ticker = {t: get_earnings(t, limit=8) for t in tickers}
    missing = [t for t, r in per_ticker.items() if not r]
    if missing:
        raise _StructuredFallback(f"no earnings rows for {missing}")

    context_summary = CMP.summarize_for_llm(tickers, per_ticker, target_quarter=target_quarter)
    quarter_hint = f" The user asked specifically about {target_quarter}." if target_quarter else ""
    user_prompt = (
        f"User question: {query}\n\n"
        f"CONTEXT SUMMARY (verified facts, use directionally):\n"
        f"{context_summary}\n"
        f"{quarter_hint}\n"
        "Respond with ONLY a JSON object matching the required schema."
    )
    t0 = time.time()
    try:
        llm_output, usage = generate_json(
            system_prompt=CMP.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=CMP.SCHEMA,
            few_shot=CMP.FEW_SHOT,
            intent_label=f"comparison:{','.join(tickers)}",
            max_tokens=1500,
        )
    except StructuredGenError as e:
        raise _StructuredFallback(f"LLM structured gen failed: {e}")

    try:
        ctx = CMP.build_context(
            tickers=tickers,
            per_ticker_earnings=per_ticker,
            llm_output=llm_output,
            target_quarter=target_quarter,
        )
        md = render(CMP.TEMPLATE, ctx)
    except RenderError as e:
        raise _StructuredFallback(f"render failed: {e}")

    logger.info(
        f"structured_gen: intent=comparison tickers={tickers} "
        f"tokens={usage.get('total_tokens')} latency={int((time.time()-t0)*1000)}ms render_ok=True"
    )
    return md


async def handle_ask_stream(req: AskRequest):
    """Streaming Q&A — returns an SSE generator.

    Fast path: price_query intent → direct DB/API lookup, no LLM (~1-2s)
    Normal path: all other intents → retrieve context → LLM generation (~50-90s)
    """
    route_result = router.route(req.query, req.tickers, req.context_tickers)
    tickers = route_result.tickers
    intent = route_result.intent
    mode = route_result.mode
    yield ("status", "🔍 Analyzing your question...")

    # ── Clarification gate (Plan D) ─────────────────────────────────
    # No ticker extracted + watchlist has multiple candidates →
    # don't call LLM; emit chips for UI to render.
    if route_result.needs_clarification:
        import json as _json
        payload = _json.dumps({
            "question": req.query,
            "options": route_result.clarification_options,
        })
        yield ("clarification", payload)
        return

    # ── Auto-bind note (Plan D, single-watchlist case) ──────────────
    if route_result.auto_bound_ticker:
        yield ("status", f"ℹ️ Assuming you mean ${route_result.auto_bound_ticker} (from your watchlist)…")

    # ── Default market tickers for ticker-less market queries ───────
    # When user asks "What happened in the market today?" with no tickers,
    # inject SPY/QQQ so we have some context to work with.
    if not tickers and intent in (Intent.NEWS_SUMMARY, Intent.GENERAL_ANALYSIS):
        logger.info("No tickers found for market query — injecting SPY, QQQ as defaults")
        tickers = ["QQQ", "SPY"]

    # ── Fast path: price_query ──────────────────────────────────────
    if intent == Intent.PRICE_QUERY and tickers:
        logger.info(f"⚡ Fast path: price_query for {tickers}")
        t0 = time.time()
        response = await _fast_price_response(tickers)
        latency = int((time.time() - t0) * 1000)
        logger.info(f"⚡ Fast path completed in {latency}ms")

        # Yield the formatted response as tokens (simulating streaming)
        # Send in chunks so the UI renders progressively
        for line in response.split("\n"):
            yield ("token", line + "\n")

        # Citations: synthetic table-style sources (no URL — frontend renders them
        # as grey labels, not links).
        fast_sources = [
            Source(
                id=f"table:price:{t}:live",
                doc_type="price_table",
                ticker=t,
                label=f"Live quote + Supabase price_snapshot — {t}",
            )
            for t in tickers
        ]
        yield ("sources", _dedupe_sources(fast_sources))
        return

    # ── Path A (Plan 4): structured generation for EARNINGS / COMPARISON ──
    # LLM produces JSON narrative; backend renders all numbers from DB.
    # On any failure (invalid JSON, schema error, render error) fall back to
    # Path B (legacy free-form generation) — guarantees user always gets an answer.
    if intent == Intent.EARNINGS_ANALYSIS and tickers:
        yield ("status", "📡 Gathering earnings data...")
        try:
            response = await _structured_earnings_response(req.query, tickers, route_result.quarter)
            for line in response.split("\n"):
                yield ("token", line + "\n")
            quarter_label = route_result.quarter or "latest"
            path_a_sources = [
                Source(
                    id=f"table:earnings:{t}:{quarter_label}",
                    doc_type="earnings_table",
                    ticker=t,
                    date=route_result.quarter,
                    label=f"Supabase earnings table — {t} ({quarter_label})",
                )
                for t in tickers
            ]
            yield ("sources", _dedupe_sources(path_a_sources))
            return
        except _StructuredFallback as sf:
            logger.warning(f"EARNINGS_ANALYSIS path A fallback: {sf}")
            # fall through to legacy retrieve+LLM path below

    if intent == Intent.COMPARISON and len(tickers) >= 2:
        yield ("status", "📡 Gathering comparison data...")
        try:
            response = await _structured_comparison_response(req.query, tickers, route_result.quarter)
            for line in response.split("\n"):
                yield ("token", line + "\n")
            quarter_label = route_result.quarter or "latest"
            path_a_sources = [
                Source(
                    id=f"table:earnings:{t}:{quarter_label}",
                    doc_type="earnings_table",
                    ticker=t,
                    date=route_result.quarter,
                    label=f"Supabase earnings table — {t} ({quarter_label})",
                )
                for t in tickers
            ]
            yield ("sources", _dedupe_sources(path_a_sources))
            return
        except _StructuredFallback as sf:
            logger.warning(f"COMPARISON path A fallback: {sf}")
            # fall through to legacy path

    # ── Normal path: retrieve + LLM ────────────────────────────────
    tier1_tickers = [t for t in tickers if is_ingested(t)]
    tier2_tickers = [t for t in tickers if t not in tier1_tickers]
    yield ("status", f"📡 Searching data for {', '.join(tickers)}...")
    all_context_parts: list[str] = []
    all_sources: list[Source] = []   # Path B citations — emitted after LLM stream
    data_freshness = ""

    # Map router-detected recency level -> numeric weight for retriever rerank.
    from config import RECENCY_WEIGHT_MAP
    recency_weight = RECENCY_WEIGHT_MAP.get(route_result.recency.value, 0.3)
    logger.info(
        f"Retrieval params: mode={mode.value}, intent={intent.value}, "
        f"recency={route_result.recency.value} (w={recency_weight}), "
        f"expanded_queries={len(route_result.expanded_queries)}"
    )

    if tier1_tickers:
        context, sources, freshness = await retrieve(
            query=req.query,
            tickers=tier1_tickers,
            mode=mode,
            intent=intent,
            recency_weight=recency_weight,
            expanded_queries=route_result.expanded_queries,
        )
        all_context_parts.append(context)
        all_sources.extend(sources)
        data_freshness = freshness

    for t in tier2_tickers:
        # tier2 (untracked tickers) goes through live API fetcher — not yet
        # producing Source objects. Skipping citations for these for now;
        # frontend will just show fewer sources.
        _maybe_promote(t)
        cached = cache.get(t, intent)
        if cached:
            all_context_parts.append(cached)
        else:
            live_context = await fetch_tier2_context(t)
            cache.put(t, intent, live_context)
            all_context_parts.append(live_context)

    full_context = "\n\n".join(all_context_parts) if all_context_parts else "(No data)"

    yield ("status", "🤖 Generating answer...")

    # Stream generation — run sync generator in thread to avoid blocking event loop.
    # The model may stream thinking chunks before content chunks. Without
    # threading, the sync for loop blocks the event loop, causing SSE
    # connections to die.
    q: queue.Queue = queue.Queue()
    SENTINEL = object()

    def _run_sync_gen():
        try:
            for token in generate_answer_stream(req.query, full_context, data_freshness):
                q.put(token)
        except Exception as e:
            logger.error(f"Generation thread error: {type(e).__name__}: {e}")
            q.put(e)
        finally:
            q.put(SENTINEL)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_sync_gen)

    while True:
        # Non-blocking poll with short sleep to keep event loop alive
        while q.empty():
            await asyncio.sleep(0.5)
            yield None  # signals keepalive to caller
        item = q.get_nowait()
        if item is SENTINEL:
            break
        if isinstance(item, Exception):
            logger.error(f"Generation thread raised: {item}")
            break
        yield item

    # Path B citations: emit after the LLM stream finishes so the UI can
    # render a sources block alongside the answer. Empty list is fine — UI
    # just hides the block when items=[].
    if all_sources:
        yield ("sources", _dedupe_sources(all_sources))
