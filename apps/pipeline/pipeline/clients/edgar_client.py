"""
SEC EDGAR API Client
Directly calls SEC official REST API (no API Key needed), provides filing documents and XBRL financial data.
Rate limit: <= 10 req/s, sleep(0.1) before each request to stay within limits.
"""

import time
import requests
from typing import Optional
from pipeline.config import EDGAR_USER_AGENT
from pipeline.utils.retry import with_retry


class EdgarClient:
    def __init__(self):
        # Reuse Session: inject User-Agent uniformly, reuse TCP connections (performance optimization)
        self.session = requests.Session()
        self.session.headers.update({
            # SEC requires: format "Name Email", for contacting developers about abusive requests
            "User-Agent": EDGAR_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        })
        # ticker→CIK mapping table, lazy-loaded and cached on first call, only one request per service lifetime
        self._ticker_cik_map: Optional[dict] = None

    @with_retry()
    def get_company_filings(
        self, cik: str, filing_type: str = "10-K", limit: int = 5
    ) -> list[dict]:
        """Fetch metadata for the most recent N filings by CIK (including download URLs).

        Args:
            cik: SEC company number, no need to pad zeros (internally auto zfill(10))
            filing_type: "10-K" (annual) or "10-Q" (quarterly)
            limit: Max number to return, default 5
        Returns:
            [{accession_no, filing_date, form_type, file_url}, ...]
        """
        url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
        time.sleep(0.1)  # Rate limit: ensure <= 10 req/s
        resp = self.session.get(url)
        resp.raise_for_status()
        data = resp.json()

        # EDGAR return structure: filings.recent contains equal-length arrays, matched by index
        filings = data.get("filings", {}).get("recent", {})
        results = []
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])
        primary_docs = filings.get("primaryDocument", [])

        for i, form in enumerate(forms):
            if form == filing_type and len(results) < limit:
                # accessionNumber format "0000320193-25-000001" → remove dashes to build URL
                acc_no = accessions[i].replace("-", "")
                results.append({
                    "accession_no": accessions[i],
                    "filing_date": dates[i],
                    "form_type": form,
                    "file_url": (
                        f"https://www.sec.gov/Archives/edgar/data/"
                        f"{cik.lstrip('0')}/{acc_no}/{primary_docs[i]}"
                    ),
                })
        return results

    @with_retry()
    def get_filing_text(self, filing_url: str) -> str:
        """Download filing full text (HTML or plain text).

        Note: data-pipeline's cleaner handles HTML tag removal and paragraph splitting.
        """
        time.sleep(0.1)  # Rate limit
        resp = self.session.get(filing_url)
        resp.raise_for_status()
        return resp.text

    @with_retry()
    def get_company_facts(self, cik: str) -> dict:
        """Fetch all XBRL structured financial data for a company (revenue/profit/EPS historical numbers).

        The returned JSON is large (typically 1-5 MB), deeply nested, data-pipeline handles parsing.
        This is the zero-cost alternative to FMP's three financial statements (income/balance/cashflow).
        """
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
        time.sleep(0.1)  # Rate limit
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    @with_retry()
    def _get_ticker_cik_map(self) -> dict:
        """Lazy-load and cache the full ticker→CIK mapping table.

        Data source: https://www.sec.gov/files/company_tickers.json
        Contains all US-listed companies (~10,000 entries), ~1 MB.
        First call makes one HTTP request, subsequent lookups use in-memory dict, O(1) complexity.
        """
        if self._ticker_cik_map is None:
            url = "https://www.sec.gov/files/company_tickers.json"
            time.sleep(0.1)  # Rate limit
            resp = self.session.get(url)
            resp.raise_for_status()
            data = resp.json()
            # Original format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
            # Convert to: {"AAPL": "320193", "MSFT": "789019", ...}
            self._ticker_cik_map = {
                v["ticker"].upper(): str(v["cik_str"])
                for v in data.values()
            }
        return self._ticker_cik_map

    def ticker_to_cik(self, ticker: str) -> Optional[str]:
        """Convert ticker to SEC CIK number.

        Args:
            ticker: Stock symbol (case-insensitive, internally converted to uppercase)
        Returns:
            CIK string (without leading zeros), e.g. "320193"; returns None if not found
        """
        mapping = self._get_ticker_cik_map()
        return mapping.get(ticker.upper())
