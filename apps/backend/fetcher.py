"""
fetcher.py
Data extraction from Supabase and context assembly for summarization / briefings.
"""

import logging
from dataclasses import dataclass, field
import db
from config import MAX_CONTEXT_CHARS

logger = logging.getLogger(__name__)


@dataclass
class TickerContext:
    ticker: str
    news_text: str = ""
    filings_text: str = ""
    earnings_text: str = ""
    price_text: str = ""
    doc_counts: dict = field(default_factory=dict)

    @property
    def total_chars(self) -> int:
        return len(self.news_text) + len(self.filings_text) + len(self.earnings_text) + len(self.price_text)


def _format_news(docs):
    if not docs:
        return ""
    lines = []
    for d in docs:
        date = d.get("date", "N/A")
        title = d.get("title") or ""
        content = d.get("content", "")
        header = f"[{date}] {title}".strip() if title else f"[{date}]"
        lines.append(f"{header}\n{content}")
    return "\n\n---\n\n".join(lines)


def _format_filings(docs):
    if not docs:
        return ""
    lines = []
    for d in docs:
        date = d.get("date", "N/A")
        doc_type = d.get("doc_type", "filing")
        source = d.get("source", "")
        section = d.get("section") or ""
        content = d.get("content", "")
        header = f"[{date}] {doc_type} ({source})"
        if section:
            header += f" - {section}"
        lines.append(f"{header}\n{content}")
    return "\n\n---\n\n".join(lines)


def _format_earnings(rows):
    if not rows:
        return ""
    lines = ["Quarter | Date | EPS | Revenue | Net Income | Guidance", "---|---|---|---|---|---"]
    for r in rows:
        eps = f"{r['eps']:.2f}" if r.get("eps") is not None else "N/A"
        rev = f"${r['revenue']:,}" if r.get("revenue") is not None else "N/A"
        ni = f"${r['net_income']:,}" if r.get("net_income") is not None else "N/A"
        guidance = r.get("guidance") or "N/A"
        lines.append(f"{r['quarter']} | {r.get('date', 'N/A')} | {eps} | {rev} | {ni} | {guidance}")
    return "\n".join(lines)


def _format_prices(rows):
    if not rows:
        return ""
    lines = ["Date | Close | P/E | Market Cap", "---|---|---|---"]
    for r in rows:
        close = f"${r['close_price']:.2f}" if r.get("close_price") is not None else "N/A"
        pe = f"{r['pe_ratio']:.1f}" if r.get("pe_ratio") is not None else "N/A"
        mc = f"${r['market_cap']:,}" if r.get("market_cap") is not None else "N/A"
        lines.append(f"{r.get('date', 'N/A')} | {close} | {pe} | {mc}")
    return "\n".join(lines)


def _truncate(ctx: TickerContext, budget: int) -> TickerContext:
    if ctx.total_chars <= budget:
        return ctx
    fixed = len(ctx.earnings_text) + len(ctx.price_text)
    remaining = budget - fixed
    fb = min(len(ctx.filings_text), remaining // 2)
    nb = remaining - fb
    if len(ctx.news_text) > nb:
        ctx.news_text = ctx.news_text[:nb] + "\n\n... [truncated]"
    if len(ctx.filings_text) > fb:
        ctx.filings_text = ctx.filings_text[:fb] + "\n\n... [truncated]"
    return ctx


def fetch_context(ticker: str, news_limit: int = 100) -> TickerContext:
    """Fetch all relevant data for a ticker and assemble into TickerContext."""
    news_docs = db.get_documents_by_ticker(ticker, doc_type="news", limit=news_limit)
    filing_docs = (
        db.get_documents_by_ticker(ticker, doc_type="10-K", limit=50)
        + db.get_documents_by_ticker(ticker, doc_type="10-Q", limit=50)
    )
    earnings_docs = db.get_documents_by_ticker(ticker, doc_type="earnings", limit=50)
    earnings_rows = db.get_earnings(ticker, limit=20)
    price_rows = db.get_price_snapshots(ticker, limit=30)

    ctx = TickerContext(
        ticker=ticker,
        news_text=_format_news(news_docs),
        filings_text=_format_filings(filing_docs + earnings_docs),
        earnings_text=_format_earnings(earnings_rows),
        price_text=_format_prices(price_rows),
        doc_counts={
            "news": len(news_docs), "filings": len(filing_docs),
            "earnings_docs": len(earnings_docs), "earnings_rows": len(earnings_rows),
            "prices": len(price_rows),
        },
    )
    return _truncate(ctx, MAX_CONTEXT_CHARS)
