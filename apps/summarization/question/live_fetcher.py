"""
question/live_fetcher.py
Tier 2 live data fetching — retrieves data for untracked tickers
via data-pipeline:8001 API.
"""

import logging
import time

from shared.formatters import format_news, format_earnings, format_prices
from shared.http import get_pipeline_client

logger = logging.getLogger(__name__)


async def fetch_live_news(ticker: str, limit: int = 10) -> str:
    """Fetch live news from data-pipeline."""
    client = get_pipeline_client()
    try:
        resp = await client.get(f"/api/finnhub/news/{ticker}", params={"limit": limit})
        resp.raise_for_status()
        data = resp.json()
        docs = []
        for item in data[:limit]:
            docs.append({
                "date": item.get("datetime", "N/A"),
                "title": item.get("headline", ""),
                "content": item.get("summary", ""),
            })
        return format_news(docs)
    except Exception as e:
        logger.warning(f"Failed to fetch live news for {ticker}: {e}")
        return ""


async def fetch_live_quote(ticker: str) -> str:
    """Fetch live quote from data-pipeline."""
    client = get_pipeline_client()
    try:
        resp = await client.get(f"/api/fmp/quote/{ticker}")
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return ""
        item = data[0] if isinstance(data, list) else data
        rows = [{
            "date": "realtime",
            "close_price": item.get("price"),
            "pe_ratio": item.get("pe"),
            "market_cap": item.get("marketCap"),
        }]
        return format_prices(rows)
    except Exception as e:
        logger.warning(f"Failed to fetch live quote for {ticker}: {e}")
        return ""


async def fetch_live_quote_raw(ticker: str) -> dict:
    """Fetch live quote from data-pipeline — returns raw dict for fast path.

    Returns full FMP quote dict (price, changePercentage, marketCap,
    dayHigh, dayLow, yearHigh, yearLow, volume, priceAvg50, priceAvg200,
    name, etc.) or empty dict on failure.
    Note: FMP quote does NOT include 'pe' field.
    """
    client = get_pipeline_client()
    try:
        resp = await client.get(f"/api/fmp/quote/{ticker}")
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return {}
        return data[0] if isinstance(data, list) else data
    except Exception as e:
        logger.warning(f"Failed to fetch live quote for {ticker}: {e}")
        return {}


async def fetch_live_earnings(ticker: str) -> str:
    """Fetch recent earnings from data-pipeline."""
    client = get_pipeline_client()
    try:
        resp = await client.get(f"/api/finnhub/earnings/{ticker}")
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for item in data[:4]:
            rows.append({
                "quarter": f"Q{item.get('quarter', '?')} {item.get('year', '')}",
                "date": item.get("date", "N/A"),
                "eps": item.get("actual"),
                "revenue": item.get("revenue"),
                "net_income": None,
                "guidance": None,
            })
        return format_earnings(rows)
    except Exception as e:
        logger.warning(f"Failed to fetch live earnings for {ticker}: {e}")
        return ""


async def fetch_tier2_context(ticker: str) -> str:
    """Fetch full Tier 2 context for an untracked ticker (news + quote + earnings)."""
    t0 = time.time()
    api_types = []

    news = await fetch_live_news(ticker)
    if news:
        api_types.append("finnhub")
    quote = await fetch_live_quote(ticker)
    if quote:
        api_types.append("fmp")
    earnings = await fetch_live_earnings(ticker)
    if earnings and "finnhub" not in api_types:
        api_types.append("finnhub")

    latency = int((time.time() - t0) * 1000)

    parts = []
    if quote:
        parts.append(f"### {ticker} Live Quote\n\n{quote}")
    if earnings:
        parts.append(f"### {ticker} Recent Earnings\n\n{earnings}")
    if news:
        parts.append(f"### {ticker} Latest News\n\n{news}")

    status = "ok" if parts else "error"
    from audit import log_api_call
    log_api_call(
        service="question",
        api_type=",".join(api_types) or "finnhub,fmp",
        endpoint=f"fetch_tier2_context/{ticker}",
        latency_ms=latency,
        status=status,
        error_msg=f"No data returned for {ticker}" if not parts else None,
    )

    return "\n\n".join(parts) if parts else f"(Unable to fetch live data for {ticker})"
