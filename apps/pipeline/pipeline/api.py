"""
pipeline/api.py
FastAPI layer — exposes pipeline's data clients as REST endpoints.

This replaces the standalone data-fetchers service.
Runs alongside the pipeline's cron job, same repo, same clients.

Usage:
    uvicorn pipeline.api:app --port 8001
"""

import logging

from fastapi import FastAPI, HTTPException

from pipeline.clients.finnhub_client import FinnhubClient
from pipeline.clients.fmp_client import FMPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="data-pipeline API",
    description="Financial data endpoints — thin wrapper over pipeline clients",
    version="0.2.0",
)

# Reuse singleton clients (same instances the cron pipeline uses)
finnhub = FinnhubClient()
fmp = FMPClient()


@app.get("/health")
def health():
    return {"status": "ok", "service": "data-pipeline"}


# ── Endpoints consumed by question/live_fetcher.py ──────────────────────────


@app.get("/api/finnhub/news/{ticker}")
def get_news(ticker: str, limit: int = 10, days_back: int = 7):
    """Fetch recent news for a ticker via Finnhub."""
    try:
        articles = finnhub.get_company_news(ticker.upper(), days_back=days_back)
        return articles[:limit]
    except Exception as e:
        logger.error(f"Finnhub news error for {ticker}: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/fmp/quote/{ticker}")
def get_quote(ticker: str):
    """Fetch real-time quote via FMP."""
    try:
        quote = fmp.get_quote(ticker.upper())
        if not quote:
            raise HTTPException(status_code=404, detail=f"No quote data for {ticker}")
        return quote
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"FMP quote error for {ticker}: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/finnhub/earnings/{ticker}")
def get_earnings(ticker: str, limit: int = 4):
    """Fetch recent earnings surprises via Finnhub."""
    try:
        return finnhub.get_earnings_surprises(ticker.upper(), limit=limit)
    except Exception as e:
        logger.error(f"Finnhub earnings error for {ticker}: {e}")
        raise HTTPException(status_code=502, detail=str(e))
