"""
FMP (Financial Modeling Prep) API Client
Wraps FMP stable API, providing real-time quotes and financial statement data.
Free tier limit: 250 req/day

Free tier actually available endpoints (verified):
  ✅ quote            Real-time quote (price/change/market cap)
  ✅ income-statement  Income statement
  ✅ balance-sheet     Balance sheet
  ✅ cash-flow         Cash flow statement

  ❌ ratios             Requires paid plan (402 Payment Required)
Note: For PE/market cap, use Finnhub get_basic_financials() instead (free, fields: peBasicExclExtraTTM / marketCapitalization).
"""

from typing import Optional, List
import requests
from pipeline.config import FMP_API_KEY
from pipeline.utils.retry import with_retry


class FMPClient:
    # FMP stable API base URL (corresponds to the free key obtained during registration)
    BASE_URL = "https://financialmodelingprep.com/stable"

    def __init__(self):
        self.api_key = FMP_API_KEY
        # Reuse Session: reduce TCP handshake overhead, consistent with EdgarClient
        self.session = requests.Session()

    @with_retry()
    def _get(self, endpoint: str, params: Optional[dict] = None) -> list:
        """Internal generic GET request, auto-injects apikey and handles HTTP errors uniformly."""
        params = params or {}
        params["apikey"] = self.api_key  # FMP requires this query parameter for all endpoints
        resp = self.session.get(f"{self.BASE_URL}/{endpoint}", params=params)
        resp.raise_for_status()  # Non-2xx status codes raise exception directly
        return resp.json()

    # ── Core endpoints (FMP-exclusive, worth spending quota) ────────────────────────────

    def get_quote(self, ticker: str) -> dict:
        """Get real-time quote (price/change%/market cap/PE).

        FMP's unique value: EDGAR doesn't have real-time prices, this is the only source.
        Returns list, takes first element (API design).
        """
        data = self._get("quote", {"symbol": ticker})
        return data[0] if data else {}

    # ── Secondary endpoints (consume 250/day quota, use EDGAR /facts for batch tasks) ──

    def get_income_statement(
        self, ticker: str, period: str = "quarter", limit: int = 4
    ) -> List[dict]:
        """[Secondary] Income statement (revenue/net income/EPS). For batch fetching, use EDGAR facts instead."""
        return self._get(
            "income-statement",
            {"symbol": ticker, "period": period, "limit": limit},
        )

    def get_balance_sheet(
        self, ticker: str, period: str = "quarter", limit: int = 4
    ) -> List[dict]:
        """[Secondary] Balance sheet. For batch fetching, use EDGAR facts instead."""
        return self._get(
            "balance-sheet-statement",
            {"symbol": ticker, "period": period, "limit": limit},
        )

    def get_cash_flow(
        self, ticker: str, period: str = "quarter", limit: int = 4
    ) -> List[dict]:
        """[Secondary] Cash flow statement. For batch fetching, use EDGAR facts instead."""
        return self._get(
            "cash-flow-statement",
            {"symbol": ticker, "period": period, "limit": limit},
        )

    def get_ratios(
        self, ticker: str, period: str = "quarter", limit: int = 4
    ) -> List[dict]:
        """[Paid] Financial ratios (current ratio/debt ratio etc.). Requires paid plan, free tier returns 402."""
        return self._get(
            "ratios",
            {"symbol": ticker, "period": period, "limit": limit},
        )
