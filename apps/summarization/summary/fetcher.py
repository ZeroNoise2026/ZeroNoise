"""
summary/fetcher.py
Extracts data from Supabase and assembles structured context for each ticker.
Formatting helpers live in shared/formatters.py — we call the shared version here.
"""

import logging
from dataclasses import dataclass, field

from shared.db import (
    get_documents_by_ticker,
    get_earnings,
    get_price_snapshots,
)
from shared.formatters import (
    format_news,
    format_regulatory,
    format_filings,
    format_earnings,
    format_prices,
)
from config import MAX_CONTEXT_CHARS

logger = logging.getLogger(__name__)


@dataclass
class TickerContext:
    ticker: str
    news_text: str = ""
    regulatory_text: str = ""
    filings_text: str = ""
    earnings_text: str = ""
    price_text: str = ""
    doc_counts: dict = field(default_factory=dict)
    # Lineage of every input that fed into this context, used by summary_cache
    # to compute input_hash. Format: ["news:<doc_id>", "filing:<doc_id>",
    # "regulatory:<doc_id>", "earnings_doc:<doc_id>",
    # "earnings_row:<ticker>:<quarter>", "price:<ticker>:<date>", ...].
    # Always sorted for deterministic hashing.
    # Invariant: documents.id is a SHA256 of content (see data-pipeline), so
    # same id => same content; we never need to hash the text bodies.
    source_doc_ids: list[str] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return (
            len(self.news_text) + len(self.regulatory_text)
            + len(self.filings_text) + len(self.earnings_text) + len(self.price_text)
        )


def _truncate_to_budget(ctx: TickerContext, budget: int) -> TickerContext:
    """If total context exceeds budget, trim news first, then filings.
    Regulatory, earnings, and price data are treated as high-priority (trimmed last).
    """
    if ctx.total_chars <= budget:
        return ctx
    fixed = len(ctx.earnings_text) + len(ctx.price_text) + len(ctx.regulatory_text)
    remaining = budget - fixed
    filings_budget = min(len(ctx.filings_text), remaining // 2)
    news_budget = remaining - filings_budget
    if len(ctx.news_text) > news_budget:
        ctx.news_text = ctx.news_text[:news_budget] + "\n\n... [truncated due to length]"
    if len(ctx.filings_text) > filings_budget:
        ctx.filings_text = ctx.filings_text[:filings_budget] + "\n\n... [truncated due to length]"
    return ctx


def fetch_context(ticker: str) -> TickerContext:
    """Fetch all relevant data from Supabase and assemble into TickerContext."""
    logger.info(f"Fetching data for {ticker}...")
    news_docs = get_documents_by_ticker(ticker, doc_type="news", limit=30)
    regulatory_docs = get_documents_by_ticker(ticker, doc_type="regulatory", limit=30)
    filing_docs = (
        get_documents_by_ticker(ticker, doc_type="10-K", limit=50)
        + get_documents_by_ticker(ticker, doc_type="10-Q", limit=50)
    )
    earnings_docs = get_documents_by_ticker(ticker, doc_type="earnings", limit=50)
    earnings_rows = get_earnings(ticker, limit=20)
    price_rows = get_price_snapshots(ticker, limit=30)
    all_filing_docs = filing_docs + earnings_docs

    # Build sorted lineage list (input_hash uses this — must be deterministic)
    source_doc_ids: list[str] = []
    source_doc_ids += [f"news:{d['id']}" for d in news_docs if d.get("id")]
    source_doc_ids += [f"regulatory:{d['id']}" for d in regulatory_docs if d.get("id")]
    source_doc_ids += [f"filing:{d['id']}" for d in filing_docs if d.get("id")]
    source_doc_ids += [f"earnings_doc:{d['id']}" for d in earnings_docs if d.get("id")]
    source_doc_ids += [
        f"earnings_row:{r.get('ticker','')}:{r.get('quarter','')}"
        for r in earnings_rows
    ]
    source_doc_ids += [
        f"price:{r.get('ticker','')}:{r.get('date','')}"
        for r in price_rows
    ]
    source_doc_ids.sort()

    ctx = TickerContext(
        ticker=ticker,
        news_text=format_news(news_docs),
        regulatory_text=format_regulatory(regulatory_docs),
        filings_text=format_filings(all_filing_docs),
        earnings_text=format_earnings(earnings_rows),
        price_text=format_prices(price_rows),
        doc_counts={
            "news": len(news_docs),
            "regulatory": len(regulatory_docs),
            "filings": len(filing_docs),
            "earnings_docs": len(earnings_docs),
            "earnings_rows": len(earnings_rows),
            "prices": len(price_rows),
        },
        source_doc_ids=source_doc_ids,
    )
    ctx = _truncate_to_budget(ctx, MAX_CONTEXT_CHARS)
    logger.info(f"  {ticker}: {ctx.doc_counts} | context chars: {ctx.total_chars:,}")
    return ctx
