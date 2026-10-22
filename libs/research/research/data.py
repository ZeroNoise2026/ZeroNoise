"""
research/data.py
Read-only queries over the ingested market data (documents, earnings,
price_snapshot). Writers live in apps/pipeline.
"""

from typing import Optional

from common import supabase

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
