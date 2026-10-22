"""
summary/cache.py
DB-backed cache for generated investment summaries.

Schema: see data-pipeline/pipeline/schema.sql `summary_cache`.
Key idea: input_hash = SHA256(sorted source doc IDs + model + prompt_version),
so an identical input always hits the cache without re-calling the LLM.

Public API:
    compute_input_hash(ctx, model, prompt_version) -> str
    get_cached(ticker, input_hash) -> Optional[CachedSummary]
    put_cached(ticker, input_hash, content, ...) -> bool

Failure policy:
    - get_cached: cache misses and DB errors both return None (safe: caller
      will regenerate).
    - put_cached: failures log a warning and return False (the summary still
      gets written to disk; cache is best-effort).
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from summary.fetcher import TickerContext

logger = logging.getLogger(__name__)


@dataclass
class CachedSummary:
    ticker: str
    input_hash: str
    summary_date: date
    content: str
    model: str
    prompt_version: str
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None


def compute_input_hash(
    ctx: TickerContext,
    model: str,
    prompt_version: str,
) -> str:
    """SHA256 over the canonical, deterministic input fingerprint.

    Anything that could change the LLM's output must be in here:
      - the sorted lineage of source doc IDs (already deterministic in
        fetch_context — but we re-sort defensively),
      - the model name,
      - the prompt template version.

    NOTE: ctx.source_doc_ids is expected to be sorted, but we sort again here
    so the hash is robust to callers building a TickerContext by hand.
    """
    canonical = "|".join([
        f"ticker={ctx.ticker.upper()}",
        f"model={model}",
        f"prompt={prompt_version}",
        "ids=" + ",".join(sorted(ctx.source_doc_ids)),
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _client():
    """Lazy Supabase client — same pattern as shared/db.py and audit/logger.py."""
    from config import SUPABASE_URL, SUPABASE_KEY
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_cached(ticker: str, input_hash: str) -> Optional[CachedSummary]:
    """Look up a cached summary by (ticker, input_hash). None on miss/error."""
    try:
        c = _client()
        if c is None:
            return None
        rows = (
            c.table("summary_cache")
            .select("ticker, input_hash, summary_date, content, model, "
                    "prompt_version, tokens_in, tokens_out")
            .eq("ticker", ticker.upper())
            .eq("input_hash", input_hash)
            .limit(1)
            .execute()
            .data
        )
        if not rows:
            return None
        r = rows[0]
        return CachedSummary(
            ticker=r["ticker"],
            input_hash=r["input_hash"],
            summary_date=date.fromisoformat(r["summary_date"]),
            content=r["content"],
            model=r["model"],
            prompt_version=r["prompt_version"],
            tokens_in=r.get("tokens_in"),
            tokens_out=r.get("tokens_out"),
        )
    except Exception as e:
        logger.warning(f"summary_cache lookup failed for {ticker} (treating as miss): {e}")
        return None


def put_cached(
    ticker: str,
    input_hash: str,
    content: str,
    model: str,
    prompt_version: str,
    source_doc_ids: list[str],
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    summary_date: Optional[date] = None,
) -> bool:
    """Insert/update a cache row. Best-effort — never raises."""
    try:
        c = _client()
        if c is None:
            logger.debug("Supabase not configured — skipping summary_cache put")
            return False
        row = {
            "ticker": ticker.upper(),
            "input_hash": input_hash,
            "summary_date": (summary_date or date.today()).isoformat(),
            "content": content,
            "model": model,
            "prompt_version": prompt_version,
            "source_doc_ids": source_doc_ids,
        }
        if tokens_in is not None:
            row["tokens_in"] = tokens_in
        if tokens_out is not None:
            row["tokens_out"] = tokens_out
        # Upsert on (ticker, input_hash). If the same hash is regenerated (e.g.
        # after a prompt-version bump and revert), we just overwrite.
        c.table("summary_cache").upsert(row, on_conflict="ticker,input_hash").execute()
        return True
    except Exception as e:
        logger.warning(f"summary_cache write failed for {ticker} (non-fatal): {e}")
        return False
