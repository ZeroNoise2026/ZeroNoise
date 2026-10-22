"""
question/live_fetcher.py
Tier 2 live data for untracked tickers, straight from the market-data APIs
(libs/marketdata). The clients are synchronous, so each call runs in a
worker thread to keep the event loop free.
"""

import asyncio
import logging
import time

from common.audit import log_api_call
from marketdata.finnhub import FinnhubClient
from marketdata.fmp import FMPClient
from research.formatters import format_news, format_earnings, format_prices

logger = logging.getLogger(__name__)

_finnhub = FinnhubClient()
_fmp = FMPClient()


async def fetch_live_news(ticker: str, limit: int = 10) -> str:
    """Recent Finnhub news, formatted for the prompt."""
    try:
        data = await asyncio.to_thread(_finnhub.get_company_news, ticker.upper())
        docs = [
            {
                "date": item.get("datetime", "N/A"),
                "title": item.get("headline", ""),
                "content": item.get("summary", ""),
            }
            for item in data[:limit]
        ]
        return format_news(docs)
    except Exception as e:
        logger.warning(f"Failed to fetch live news for {ticker}: {e}")
        return ""


async def fetch_live_quote_raw(ticker: str) -> dict:
    """Full FMP quote dict (price, changePercentage, marketCap, dayHigh, dayLow,
    yearHigh, yearLow, volume, priceAvg50, priceAvg200, name, …), or {} on
    failure. Note: FMP quote does NOT include a 'pe' field.
    """
    try:
        data = await asyncio.to_thread(_fmp.get_quote, ticker.upper())
        if not data:
            return {}
        return data[0] if isinstance(data, list) else data
    except Exception as e:
        logger.warning(f"Failed to fetch live quote for {ticker}: {e}")
        return {}


async def fetch_live_quote(ticker: str) -> str:
    """Live FMP quote, formatted for the prompt."""
    item = await fetch_live_quote_raw(ticker)
    if not item:
        return ""
    return format_prices([{
        "date": "realtime",
        "close_price": item.get("price"),
        "pe_ratio": item.get("pe"),
        "market_cap": item.get("marketCap"),
    }])


async def fetch_live_earnings(ticker: str) -> str:
    """Recent Finnhub earnings surprises, formatted for the prompt."""
    try:
        data = await asyncio.to_thread(_finnhub.get_earnings_surprises, ticker.upper())
        rows = [
            {
                "quarter": f"Q{item.get('quarter', '?')} {item.get('year', '')}",
                "date": item.get("date", "N/A"),
                "eps": item.get("actual"),
                "revenue": item.get("revenue"),
                "net_income": None,
                "guidance": None,
            }
            for item in data[:4]
        ]
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
    log_api_call(
        service="question",
        api_type=",".join(api_types) or "finnhub,fmp",
        endpoint=f"fetch_tier2_context/{ticker}",
        latency_ms=latency,
        status=status,
        error_msg=f"No data returned for {ticker}" if not parts else None,
    )

    return "\n\n".join(parts) if parts else f"(Unable to fetch live data for {ticker})"
