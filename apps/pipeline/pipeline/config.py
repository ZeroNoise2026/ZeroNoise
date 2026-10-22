"""
pipeline/config.py
Load all service URLs, API keys, and credentials from .env file.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────

# Finnhub API Key — for news, earnings, financial metrics; free 60 req/min
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# FMP API Key — for real-time quotes, valuation metrics; free 250 req/day (use sparingly)
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# SEC EDGAR User-Agent — format: "Name Email", SEC requires real contact info, no registration needed
EDGAR_USER_AGENT = os.getenv("EDGAR_USER_AGENT", "QuantAgent dev@quantagent.com")

# ── Upstream service URLs ─────────────────────────────────────────────────────

EMBEDDING_SERVICE_URL = os.getenv("EMBEDDING_SERVICE_URL", "http://localhost:8002")

# ── Supabase ──────────────────────────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "")   # https://xxx.supabase.co
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")   # service_role key (used for DB writes)

# Fallback ticker list when Supabase is not configured
DEFAULT_TICKERS = os.getenv("DEFAULT_TICKERS", "AAPL,MSFT,NVDA").split(",")

# ── Pipeline tuning ──────────────────────────────────────────────────────────

# API quota protection
FILING_REFRESH_DAYS = int(os.getenv("FILING_REFRESH_DAYS", "30"))   # FMP/EDGAR re-fetch interval (days)
EDGAR_FACTS_MAX_MB  = float(os.getenv("EDGAR_FACTS_MAX_MB", "15"))  # EDGAR facts JSON size limit (MB)

# Batch sizes (can be increased as Supabase/embedding-service scales up)
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "100"))   # max chunks per embedding request
BATCH_SIZE  = int(os.getenv("BATCH_SIZE",  "100"))   # max rows per DB write

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
