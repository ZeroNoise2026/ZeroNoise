"""
question/db.py
Supabase queries owned by the question flow: tracked-ticker bookkeeping,
hot-ticker promotion and vector search. Market-data reads (documents,
earnings, prices, tracked tickers) live in research.data.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from common.supabase import get_client as _get_client
from research.data import FIELDS_DOCUMENTS

logger = logging.getLogger(__name__)


def is_tracked(ticker: str) -> bool:
    """Check if a ticker exists in tracked_tickers and is active."""
    client = _get_client()
    rows = (
        client.table("tracked_tickers")
        .select("ticker")
        .eq("ticker", ticker.upper())
        .eq("is_active", True)
        .limit(1)
        .execute()
        .data
    )
    return len(rows) > 0


def is_ingested(ticker: str) -> bool:
    """Check if a ticker is tracked AND the data-pipeline has ingested it at least once.

    A freshly promoted ticker is tracked but has no documents until the next
    pipeline run; routing it to Tier 1 before then would retrieve nothing.
    """
    client = _get_client()
    rows = (
        client.table("tracked_tickers")
        .select("ticker")
        .eq("ticker", ticker.upper())
        .eq("is_active", True)
        .not_.is_("last_news_fetch", "null")
        .limit(1)
        .execute()
        .data
    )
    return len(rows) > 0


def record_ticker_query(ticker: str) -> None:
    """Log one query for an untracked ticker (shared across service instances).

    Note: Requires scripts/setup_ticker_queries.sql
    """
    client = _get_client()
    try:
        client.table("ticker_queries").insert({"ticker": ticker.upper()}).execute()
    except Exception as e:
        # Table may not exist yet — don't block main flow
        logger.warning(f"Failed to record query for {ticker}: {e}")


def count_recent_ticker_queries(ticker: str, hours: int = 24) -> int:
    """Count queries for a ticker in the last `hours` hours. Returns 0 on failure."""
    client = _get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        resp = (
            client.table("ticker_queries")
            .select("id", count="exact")
            .eq("ticker", ticker.upper())
            .gte("queried_at", cutoff)
            .limit(1)
            .execute()
        )
        return resp.count or 0
    except Exception as e:
        logger.warning(f"Failed to count queries for {ticker}: {e}")
        return 0


def update_last_queried(ticker: str) -> None:
    """Update tracked_tickers.last_queried timestamp (used for demotion logic).

    Note: Requires ALTER TABLE tracked_tickers ADD COLUMN last_queried TIMESTAMPTZ;
    See scripts/setup_match_documents.sql
    """
    client = _get_client()
    try:
        client.table("tracked_tickers").update(
            {"last_queried": "now()"}
        ).eq("ticker", ticker.upper()).execute()
    except Exception as e:
        # last_queried column may not exist yet — don't block main flow
        logger.warning(f"Failed to update last_queried for {ticker}: {e}")


def promote_ticker(ticker: str, ticker_type: str = "stock") -> None:
    """Hot promotion: insert an untracked ticker into tracked_tickers.

    The data-pipeline will start fetching data for this ticker on its next run.
    Until then is_ingested() keeps it on the Tier 2 live-fetch path.
    """
    client = _get_client()
    try:
        client.table("tracked_tickers").upsert({
            "ticker": ticker.upper(),
            "ticker_type": ticker_type,
            "is_active": True,
        }).execute()
        logger.info(f"Promoted {ticker} to tracked_tickers (type={ticker_type})")
    except Exception as e:
        logger.error(f"Failed to promote {ticker}: {e}")


def get_documents_by_embedding(
    query_embedding: list[float],
    ticker: Optional[str] = None,
    doc_type: Optional[str] = None,
    top_k: int = 8,
    threshold: float = 0.3,
) -> list[dict]:
    """Semantic search on the documents table via pgvector.

    Calls the Supabase RPC function `match_documents`.
    See scripts/setup_match_documents.sql to create this function.
    """
    client = _get_client()
    params = {
        "query_embedding": query_embedding,
        "match_count": top_k,
        "similarity_threshold": threshold,
    }
    if ticker:
        params["filter_ticker"] = ticker.upper()
    if doc_type:
        params["filter_doc_type"] = doc_type

    result = client.rpc("match_documents", params).execute()
    return result.data or []
