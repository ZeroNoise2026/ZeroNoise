"""
question/retriever.py
Data retrieval layer — Tier 1 (DB semantic search + structured queries).
"""

import logging
import math
import time
from datetime import date as _date, datetime as _dt
from typing import Optional

from config import (
    SEMANTIC_TOP_K,
    SIMILARITY_THRESHOLD,
    RECENCY_HALF_LIFE_DAYS,
    RECENCY_OVERFETCH_MULTIPLIER,
    EXPANSION_TOP_K_PER_QUERY,
)
from question.models import Intent, QueryMode, Source
from shared.db import (
    get_documents_by_embedding,
    get_earnings,
    get_price_snapshots,
    get_tracked_tickers,
)
from shared.formatters import (
    format_news,
    format_filings,
    format_earnings,
    format_prices,
)
from shared.http import get_embedding_client

logger = logging.getLogger(__name__)


async def _encode_query(query: str) -> list[float]:
    """Call embedding-service to convert query text into a 384-dim vector."""
    t0 = time.time()
    client = get_embedding_client()
    resp = await client.post(
        "/api/encode-query",
        json={"query": query},
    )
    resp.raise_for_status()
    latency = int((time.time() - t0) * 1000)

    from audit import log_api_call
    log_api_call(
        service="question",
        api_type="embedding",
        endpoint="/api/encode-query",
        latency_ms=latency,
    )
    return resp.json()["embedding"]


async def _encode_queries(queries: list[str]) -> list[list[float]]:
    """Batch-encode multiple query strings into 384-dim vectors via /api/encode.

    Single HTTP round-trip; preserves input order. Used for query-expansion
    multi-vector retrieval. On failure, raises — caller decides fallback.
    """
    if not queries:
        return []
    t0 = time.time()
    client = get_embedding_client()
    resp = await client.post(
        "/api/encode",
        json={"texts": queries},
    )
    resp.raise_for_status()
    latency = int((time.time() - t0) * 1000)

    from audit import log_api_call
    log_api_call(
        service="question",
        api_type="embedding",
        endpoint="/api/encode",
        latency_ms=latency,
    )
    embeddings = resp.json()["embeddings"]
    logger.info(f"Batch-encoded {len(queries)} queries in {latency}ms")
    return embeddings


async def semantic_search(
    query: str,
    ticker: Optional[str] = None,
    doc_type: Optional[str] = None,
    recency_weight: float = 0.0,
    expanded_queries: Optional[list[str]] = None,
) -> tuple[list[dict], list[Source]]:
    """Semantic search: query -> embedding -> pgvector -> recency rerank -> docs + sources.

    Args:
        query: User question text.
        ticker: Optional ticker filter.
        doc_type: Optional doc_type filter (e.g. "news").
        recency_weight: 0.0 = pure cosine; >0 = blend with recency_decay.
            See config.RECENCY_WEIGHT_MAP for typical values per Recency level.
            When 0, behaviour is identical to the pre-rerank implementation.
        expanded_queries: Optional list of LLM-generated alt phrasings of `query`.
            When non-empty, runs multi-vector retrieval (batch-encode all queries,
            pgvector lookup per embedding, merge by doc id keeping max cosine),
            then reranks the merged candidate pool. Used to overcome embedding
            bias on news queries where 'latest news on X' matches generic
            'trending stock' articles instead of concrete same-day headlines.
    """
    expanded_queries = expanded_queries or []
    docs: list[dict] = []

    # ── Path A: multi-vector (query expansion active) ──
    if expanded_queries:
        try:
            all_queries = [query] + expanded_queries
            all_embeddings = await _encode_queries(all_queries)
            docs = _multi_vector_retrieve(
                embeddings=all_embeddings,
                ticker=ticker,
                doc_type=doc_type,
                top_k_per_query=EXPANSION_TOP_K_PER_QUERY,
            )
            logger.info(
                f"Multi-vector retrieval: {len(all_queries)} queries -> "
                f"{len(docs)} unique candidates (ticker={ticker}, doc_type={doc_type})"
            )
        except Exception as e:
            logger.warning(f"Multi-vector retrieval failed, falling back to single-vector: {e}")
            expanded_queries = []  # force fallback below

    # ── Path B: single-vector (default / fallback) ──
    if not expanded_queries:
        embedding = await _encode_query(query)

        # Over-fetch when reranking is on, so the rerank can actually re-order.
        fetch_k = SEMANTIC_TOP_K
        if recency_weight > 0.0:
            fetch_k = SEMANTIC_TOP_K * RECENCY_OVERFETCH_MULTIPLIER

        docs = get_documents_by_embedding(
            query_embedding=embedding,
            ticker=ticker,
            doc_type=doc_type,
            top_k=fetch_k,
            threshold=SIMILARITY_THRESHOLD,
        )

    if recency_weight > 0.0 and docs:
        docs = _apply_recency_rerank(docs, recency_weight)[:SEMANTIC_TOP_K]
    else:
        docs = docs[:SEMANTIC_TOP_K]

    sources = []
    for d in docs:
        # documents.id is a deterministic SHA256 (see data-pipeline/store.make_document_id),
        # so the same chunk always gets the same Source.id across runs.
        doc_id = d.get("id") or ""
        sources.append(Source(
            id=f"doc:{doc_id}" if doc_id else f"doc:unknown:{d.get('ticker','?')}:{d.get('date','?')}",
            doc_type=d.get("doc_type", "news"),
            ticker=d.get("ticker", ""),
            date=d.get("date"),
            title=d.get("title"),
            url=d.get("url"),  # NULL for filings/earnings doc rows; present for news after schema migration
            similarity=d.get("similarity"),
        ))

    return docs, sources


def _multi_vector_retrieve(
    embeddings: list[list[float]],
    ticker: Optional[str],
    doc_type: Optional[str],
    top_k_per_query: int,
) -> list[dict]:
    """Run pgvector lookup once per embedding, merge by doc id keeping max cosine.

    Returns deduped candidate pool (typically 30-60 docs for 5 queries×16 each).
    Does NOT slice to SEMANTIC_TOP_K — caller's rerank will do that.
    """
    from typing import Any
    merged: dict[Any, dict] = {}
    for emb in embeddings:
        rows = get_documents_by_embedding(
            query_embedding=emb,
            ticker=ticker,
            doc_type=doc_type,
            top_k=top_k_per_query,
            threshold=SIMILARITY_THRESHOLD,
        )
        for r in rows:
            doc_id = r.get("id") or r.get("document_id") or r.get("chunk_id")
            if doc_id is None:
                # Fallback: hash by (ticker, date, title) — best effort
                doc_id = (r.get("ticker"), r.get("date"), r.get("title"))
            existing = merged.get(doc_id)
            new_sim = float(r.get("similarity") or 0.0)
            if existing is None or new_sim > float(existing.get("similarity") or 0.0):
                merged[doc_id] = r
    return list(merged.values())


def _coerce_date(val) -> Optional[_date]:
    """Best-effort conversion of date-ish values from Supabase into a date."""
    if val is None:
        return None
    if isinstance(val, _date) and not isinstance(val, _dt):
        return val
    if isinstance(val, _dt):
        return val.date()
    try:
        return _dt.fromisoformat(str(val)[:10]).date()
    except (ValueError, TypeError):
        return None


def _apply_recency_rerank(docs: list[dict], recency_weight: float) -> list[dict]:
    """Blend cosine similarity with a Gaussian recency decay and re-sort in place.

    final = (1 - w) * cosine_sim + w * recency_decay
    recency_decay = exp( -ln(2) * (age_days / half_life)^2 )
      → a doc exactly half_life days old contributes weight 0.5.
    """
    today = _date.today()
    half_life = max(RECENCY_HALF_LIFE_DAYS, 1.0)
    ln2 = math.log(2.0)

    for d in docs:
        cosine_sim = float(d.get("similarity") or 0.0)
        doc_date = _coerce_date(d.get("date"))
        if doc_date is None:
            recency = 0.0  # missing date = treat as oldest possible
        else:
            age_days = max((today - doc_date).days, 0)
            recency = math.exp(-ln2 * (age_days / half_life) ** 2)
        d["recency_score"] = recency
        d["final_score"] = (1.0 - recency_weight) * cosine_sim + recency_weight * recency

    docs.sort(key=lambda d: d.get("final_score", 0.0), reverse=True)
    if logger.isEnabledFor(logging.DEBUG):
        top_preview = [
            f"[{d.get('date')}] cos={d.get('similarity'):.2f} rec={d.get('recency_score'):.2f} fin={d.get('final_score'):.2f}"
            for d in docs[:5]
        ]
        logger.debug(f"recency rerank (w={recency_weight}): top5 -> {top_preview}")
    return docs


def structured_lookup(
    ticker: str,
    intent: Intent,
) -> tuple[str, list[Source]]:
    """Structured query: fetch data directly from DB tables (earnings, price_snapshot)."""
    sources: list[Source] = []
    parts: list[str] = []

    if intent in (Intent.EARNINGS_ANALYSIS, Intent.TRADE_SUGGESTION, Intent.COMPARISON, Intent.GENERAL_ANALYSIS):
        rows = get_earnings(ticker, limit=8)
        if rows:
            parts.append(f"### {ticker} Earnings\n\n{format_earnings(rows)}")
            latest_q = rows[0].get("quarter") or rows[0].get("date") or "latest"
            sources.append(Source(
                id=f"table:earnings:{ticker}:{latest_q}",
                doc_type="earnings_table",
                ticker=ticker,
                date=rows[0].get("date"),
                label=f"Supabase earnings table — {ticker} ({len(rows)} quarters, latest {latest_q})",
            ))

    if intent in (Intent.PRICE_QUERY, Intent.TRADE_SUGGESTION, Intent.COMPARISON, Intent.GENERAL_ANALYSIS):
        rows = get_price_snapshots(ticker, limit=5)
        if rows:
            parts.append(f"### {ticker} Price Snapshot\n\n{format_prices(rows)}")
            latest_d = rows[0].get("date") or "latest"
            sources.append(Source(
                id=f"table:price:{ticker}:{latest_d}",
                doc_type="price_table",
                ticker=ticker,
                date=latest_d,
                label=f"Supabase price_snapshot — {ticker} (latest {latest_d})",
            ))

    return "\n\n".join(parts), sources


async def retrieve(
    query: str,
    tickers: list[str],
    mode: QueryMode,
    intent: Intent,
    recency_weight: float = 0.0,
    expanded_queries: Optional[list[str]] = None,
) -> tuple[str, list[Source], str]:
    """Main retrieval entry point. Dispatches to semantic / structured / hybrid based on mode.

    Args:
        recency_weight: 0.0 = pure cosine (legacy behaviour). >0 enables Gaussian
            recency rerank inside semantic_search. Mapped from RouterResult.recency
            via config.RECENCY_WEIGHT_MAP — done by the caller (orchestrator).
        expanded_queries: Optional LLM-generated alternative phrasings used for
            multi-vector retrieval (combats embedding bias). Passed through to
            semantic_search; ignored for structured-only modes.

    Returns:
        (context_text, sources, data_freshness)
    """
    all_parts: list[str] = []
    all_sources: list[Source] = []
    data_freshness = ""

    # Get data freshness from tracked_tickers
    tracked = get_tracked_tickers(active_only=True)
    tracked_map = {r["ticker"]: r for r in tracked}
    for t in tickers:
        info = tracked_map.get(t)
        if info and info.get("last_successful_run"):
            data_freshness = info["last_successful_run"]
            break

    # Semantic search
    if mode in (QueryMode.SEMANTIC, QueryMode.HYBRID):
        for ticker in tickers:
            doc_type = None
            if intent == Intent.NEWS_SUMMARY:
                doc_type = "news"
            docs, sources = await semantic_search(
                query,
                ticker=ticker,
                doc_type=doc_type,
                recency_weight=recency_weight,
                expanded_queries=expanded_queries,
            )
            if docs:
                text_parts = []
                for d in docs:
                    header = f"[{d.get('date', 'N/A')}] {d.get('title', '')}".strip()
                    text_parts.append(f"{header}\n{d.get('content', '')}")
                all_parts.append(f"### {ticker} Related Documents\n\n" + "\n\n---\n\n".join(text_parts))
                all_sources.extend(sources)

    # Structured query
    if mode in (QueryMode.STRUCTURED, QueryMode.HYBRID):
        for ticker in tickers:
            text, sources = structured_lookup(ticker, intent)
            if text:
                all_parts.append(text)
                all_sources.extend(sources)

    context = "\n\n".join(all_parts) if all_parts else "(No relevant data found)"
    return context, all_sources, data_freshness
