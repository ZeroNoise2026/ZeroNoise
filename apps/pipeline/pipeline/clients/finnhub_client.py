"""
Finnhub API Client
Wraps the Finnhub official Python SDK, providing news, earnings, and financial metrics fetching.
Free tier limit: 60 req/min, returns 429 when exceeded.
"""

import finnhub
from datetime import datetime, timedelta
from pipeline.config import FINNHUB_API_KEY
from pipeline.utils.retry import with_retry


class FinnhubClient:
    def __init__(self):
        # Initialize using the official SDK, which manages HTTP connections internally
        self.client = finnhub.Client(api_key=FINNHUB_API_KEY)

    @with_retry()
    def get_company_news(self, ticker: str, days_back: int = 7) -> list[dict]:
        """Fetch recent news articles for the given ticker.

        Args:
            ticker: Stock symbol, must be uppercase (e.g. "AAPL")
            days_back: Number of days to look back, default 7, max 365
        Returns:
            List of news articles, each containing headline / summary / url / datetime fields
        """
        today = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        return self.client.company_news(ticker, _from=start, to=today)

    @with_retry()
    def get_earnings_surprises(self, ticker: str, limit: int = 4) -> list[dict]:
        """Fetch recent N quarters of Earnings Surprise (actual EPS vs estimate).

        Core signal source for determining "beat/miss" in the RAG system.
        Returns:
            Earnings list sorted by time descending, containing actual / estimate / surprisePercent
        """
        return self.client.company_earnings(ticker, limit=limit)

    @with_retry()
    def get_basic_financials(self, ticker: str) -> dict:
        """Fetch basic financial metrics (PE, market cap, 52-week high/low, Beta, etc.).

        Note: Only returns the metric field (latest snapshot), excludes series historical data.
        FMP key-metrics requires paid plan (402), this endpoint is the free alternative for PE/market cap.
        """
        data = self.client.company_basic_financials(ticker, "all")
        # SDK returns {"metric": {...}, "series": {...}}, only take the latest snapshot
        return data.get("metric", {})

    @with_retry()
    def get_quote(self, ticker: str) -> dict:
        """Get real-time quote (price / change / open-high-low-close).

        Supports both individual stocks and ETFs (VOO/SPY/QQQ all work).
        FMP quote returns 402 for ETFs, use this endpoint instead.
        Return fields: c=current, pc=prev close, d=change, dp=change%, h=high, l=low
        """
        return self.client.quote(ticker)

    @with_retry()
    def get_ticker_type(self, ticker: str) -> str:
        """Auto-detect ticker type: 'stock' or 'etf'.

        Detection logic:
          1. company_profile2 returns data → stock (has company info)
          2. company_profile2 empty + quote.c > 0 → ETF (has price but no company profile)
          3. Both empty → invalid ticker, raises ValueError

        Only needs to be called once when first adding a ticker, result stored in tracked_tickers.ticker_type.
        """
        profile = self.client.company_profile2(symbol=ticker)
        if profile:
            return "stock"
        # profile is empty: ETF or invalid ticker, validate with real-time quote
        quote = self.client.quote(ticker)
        if quote.get("c", 0) > 0:
            return "etf"
        raise ValueError(f"Ticker '{ticker}' not found: no profile and no price data")

    @with_retry()
    def get_company_profile(self, ticker: str) -> dict:
        """Fetch company basic info (name / industry / country / logo URL etc.).

        Only valid for individual stocks, ETFs return {}.
        Typically called once when user first adds a ticker, result can be cached.
        """
        return self.client.company_profile2(symbol=ticker)
