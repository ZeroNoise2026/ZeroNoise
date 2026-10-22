"""
pipeline/config.py
Load all service URLs, API keys, and credentials from .env file.
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

# The single repo-root .env: the nearest .env above this file. Real environment
# variables (Cloud Run) win; in a container there is no file and this is a no-op.
load_dotenv(next((d / ".env" for d in Path(__file__).resolve().parents if (d / ".env").is_file()), None))

# Finnhub / FMP / EDGAR credentials are read by libs/marketdata from the
# environment (FINNHUB_API_KEY, FMP_API_KEY, EDGAR_USER_AGENT).

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
