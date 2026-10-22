"""
pipeline/store.py
Supabase write layer: writes via supabase-py REST API (HTTPS), no direct PostgreSQL connection needed.

Design principles:
  1. Idempotent: document id = SHA256(ticker+date+content[:100])[:16]
  2. Upsert = INSERT ... ON CONFLICT DO UPDATE, safe for daily re-runs without duplicates
  3. Batch 100 rows/request, reducing database round trips
  4. When SUPABASE_KEY is not configured, automatically falls back to dry_run mode for local debugging
  5. Vectors are passed as string '[x1,x2,...]' to PostgREST, pgvector auto-converts
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Optional

from supabase import create_client, Client

from pipeline.config import SUPABASE_URL, SUPABASE_KEY, DEFAULT_TICKERS, BATCH_SIZE

logger = logging.getLogger(__name__)
_client: Optional[Client] = None  # module-level singleton, avoids repeated initialization


def _get_client() -> Optional[Client]:
    """Get Supabase client singleton. Returns None if SUPABASE_KEY is not configured."""
    global _client
    if _client is not None:
        return _client
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase client initialized (REST API)")
        return _client
    except Exception as e:
        logger.warning(f"Supabase client init failed: {e}")
        return None


def _vec_str(v: list) -> str:
    """Convert float list to pgvector format string '[x1,x2,...]'."""
    return "[" + ",".join(str(x) for x in v) + "]"


def make_document_id(ticker: str, date: str, content: str) -> str:
    """Generate deterministic document ID = SHA256(ticker+date+content[:100])[:16]."""
    raw = f"{ticker}{date}{content[:100]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def upsert_documents(rows: List[dict], dry_run: bool = False) -> int:
    """Batch upsert to the documents table (including embedding vectors)."""
    if dry_run or not SUPABASE_KEY:
        logger.info(f"[dry_run] upsert {len(rows)} documents")
        for r in rows[:2]:
            logger.info(f"  {r['ticker']} | {r['doc_type']} | {r['date']} | id={r['id']}")
        return len(rows)

    client = _get_client()
    if not client:
        return 0

    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i: i + BATCH_SIZE]
        try:
            formatted = []
            for r in batch:
                row = dict(r)
                # PostgREST accepts vector string '[x1,x2,...]', pgvector auto-converts
                if row.get("embedding") is not None:
                    row["embedding"] = _vec_str(row["embedding"])
                formatted.append(row)
            resp = client.table("documents").upsert(formatted).execute()
            total += len(resp.data)
            logger.info(f"Upserted {len(resp.data)} docs (batch {i // BATCH_SIZE + 1})")
        except Exception as e:
            logger.error(f"documents batch {i // BATCH_SIZE + 1} failed: {e}")
    return total


def upsert_earnings(rows: List[dict], dry_run: bool = False) -> int:
    """Batch upsert to the earnings table. Conflict key: (ticker, quarter)"""
    if dry_run or not SUPABASE_KEY:
        logger.info(f"[dry_run] upsert {len(rows)} earnings rows")
        return len(rows)

    client = _get_client()
    if not client:
        return 0

    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i: i + BATCH_SIZE]
        try:
            resp = client.table("earnings").upsert(batch).execute()
            total += len(resp.data)
        except Exception as e:
            logger.error(f"earnings batch failed: {e}")
    logger.info(f"upsert_earnings: {total}/{len(rows)} rows written")
    return total


def upsert_price_snapshot(rows: List[dict], dry_run: bool = False) -> int:
    """Batch upsert to the price_snapshot table. Conflict key: (ticker, date)"""
    if dry_run or not SUPABASE_KEY:
        logger.info(f"[dry_run] upsert {len(rows)} price snapshots")
        return len(rows)

    client = _get_client()
    if not client:
        return 0

    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i: i + BATCH_SIZE]
        try:
            resp = client.table("price_snapshot").upsert(batch).execute()
            total += len(resp.data)
        except Exception as e:
            logger.error(f"price_snapshot batch failed: {e}")
    logger.info(f"upsert_price_snapshot: {total}/{len(rows)} rows written")
    return total


def get_tracked_tickers() -> List[dict]:
    """Read all is_active=true tickers from Supabase.

    Returns: List[{"ticker": str, "ticker_type": str|None, ...}]
    Falls back to DEFAULT_TICKERS if Supabase is not configured.
    """
    client = _get_client()
    if not client:
        logger.warning("No Supabase client — using DEFAULT_TICKERS from config")
        return [{"ticker": t, "ticker_type": None} for t in DEFAULT_TICKERS]

    try:
        resp = (
            client.table("tracked_tickers")
            .select("ticker, ticker_type, last_news_fetch, last_filing_fetch")
            .eq("is_active", True)
            .order("ticker")
            .execute()
        )
        rows = resp.data
        if not rows:
            logger.warning("tracked_tickers is empty — using DEFAULT_TICKERS")
            return [{"ticker": t, "ticker_type": None} for t in DEFAULT_TICKERS]
        return list(rows)  # type: ignore[return-value]
    except Exception as e:
        logger.error(f"get_tracked_tickers failed: {e}")
        return [{"ticker": t, "ticker_type": None} for t in DEFAULT_TICKERS]


def update_ticker_timestamps(
    ticker: str,
    news_fetch: Optional[str] = None,
    filing_fetch: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """Update tracked_tickers timestamps for Person B to display data freshness.

    last_news_fetch     → date of most recent successful news fetch
    last_filing_fetch   → date of most recent successful FMP/EDGAR filing fetch
    last_successful_run → timestamp of most recent complete processing run (UTC)
    Non-fatal operation: only logs a warning on failure, doesn't interrupt the main flow.
    """
    if dry_run or not SUPABASE_KEY:
        logger.info(f"[dry_run] timestamps {ticker}: news={news_fetch}, filing={filing_fetch}")
        return

    client = _get_client()
    if not client:
        return

    updates: dict = {}
    if news_fetch:
        updates["last_news_fetch"] = news_fetch
    if filing_fetch:
        updates["last_filing_fetch"] = filing_fetch

    if not updates:
        return  # nothing to update, return directly

    try:
        client.table("tracked_tickers").update(updates).eq("ticker", ticker).execute()
        logger.debug(f"Updated timestamps for {ticker}")
    except Exception as e:
        logger.warning(f"Failed to update timestamps for {ticker}: {e}")
