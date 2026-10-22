"""
research/data.py
Read-only queries over the ingested market data (documents, earnings,
price_snapshot, tracked_tickers). Writers live in apps/pipeline.
"""

import logging
from typing import Optional

from common import supabase

logger = logging.getLogger(__name__)

FIELDS_DOCUMENTS = "id, content, ticker, date, source, doc_type, section, title, url"


def get_documents_by_ticker(
    ticker: str,
    doc_type: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Fetch documents for a ticker, ordered by date DESC."""
    client = supabase.get_client()
    query = (
        client.table("documents")
        .select(FIELDS_DOCUMENTS)
        .eq("ticker", ticker)
        .order("date", desc=True)
        .limit(limit)
    )
    if doc_type:
        query = query.eq("doc_type", doc_type)
    return query.execute().data


def get_earnings(ticker: str, limit: int = 20) -> list[dict]:
    """Fetch earnings rows for a ticker, ordered by date DESC."""
    client = supabase.get_client()
    return (
        client.table("earnings")
        .select("ticker, quarter, date, eps, revenue, net_income, guidance")
        .eq("ticker", ticker)
        .order("date", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def get_price_snapshots(ticker: str, limit: int = 30) -> list[dict]:
    """Fetch recent price snapshots for a ticker, ordered by date DESC."""
    client = supabase.get_client()
    return (
        client.table("price_snapshot")
        .select("ticker, date, close_price, pe_ratio, market_cap")
        .eq("ticker", ticker)
        .order("date", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def get_tracked_tickers(active_only: bool = True) -> list[dict]:
    """Fetch tracked tickers from Supabase.

    Falls back to a minimal SELECT if optional columns (last_successful_run, etc.)
    have not been added to the table yet.
    """
    client = supabase.get_client()
    try:
        query = (
            client.table("tracked_tickers")
            .select("ticker, ticker_type, last_news_fetch, last_filing_fetch, last_successful_run")
            .order("ticker")
        )
        if active_only:
            query = query.eq("is_active", True)
        return query.execute().data
    except Exception as e:
        if "does not exist" in str(e):
            logger.warning(f"Falling back to minimal tracked_tickers SELECT: {e}")
            query = (
                client.table("tracked_tickers")
                .select("ticker, ticker_type, last_news_fetch, last_filing_fetch")
                .order("ticker")
            )
            if active_only:
                query = query.eq("is_active", True)
            return query.execute().data
        raise
