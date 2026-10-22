"""
config.py
Environment for apps/summarization (question service + summary CLI).

Importing this module loads the repo-root .env. Credentials (SUPABASE_URL, SUPABASE_KEY,
ANTHROPIC_API_KEY) and the report settings (LLM_MODEL_SUMMARY,
LLM_EFFORT_SUMMARY, MAX_CONTEXT_CHARS) are read from the environment by
libs/common and libs/research; only app-specific settings live here.

Sections:
  1. Summary CLI
  2. Question-service LLM settings
  3. Upstream service URLs
  4. Vector search parameters
  5. Tier 2 cache parameters
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# The single repo-root .env: the nearest .env above this file. Real environment
# variables (Cloud Run) win; in a container there is no file and this is a no-op.
load_dotenv(next((d / ".env" for d in Path(__file__).resolve().parents if (d / ".env").is_file()), None))

# ═══════════════════════════════════════════
# 1. Summary CLI
# ═══════════════════════════════════════════
OUTPUT_DIR = Path(__file__).parent / "output"

# ═══════════════════════════════════════════
# 2. Question-service LLM settings
# ═══════════════════════════════════════════
LLM_MODEL_QA: str = os.getenv("LLM_MODEL_QA", "claude-haiku-5-5")
LLM_MODEL_CLASSIFY: str = os.getenv("LLM_MODEL_CLASSIFY", "claude-haiku-5-5")
# "low" | "medium" | "high" — thinking depth vs. latency for the QA stream
LLM_EFFORT_QA: str = os.getenv("LLM_EFFORT_QA", "medium")
# Includes thinking tokens, so keep well above the expected answer length
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "16000"))

# ═══════════════════════════════════════════
# 3. Upstream service URLs
# ═══════════════════════════════════════════
EMBEDDING_SERVICE_URL: str = os.getenv("EMBEDDING_SERVICE_URL", "http://localhost:8002")
DATA_PIPELINE_URL: str = os.getenv("DATA_PIPELINE_URL", "http://localhost:8001")

# ═══════════════════════════════════════════
# 4. Vector search parameters
# ═══════════════════════════════════════════
SEMANTIC_TOP_K: int = int(os.getenv("SEMANTIC_TOP_K", "8"))
SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.3"))

# ── Recency-aware reranking (financial news bias) ──
# Soft weighting: final = (1-w)*cosine + w*recency_decay
# recency_decay = exp(-ln(2) * (age_days / half_life)^2)  Gaussian half-life curve
# half_life=14d -> a 14-day-old doc gets weight 0.5 vs a fresh doc.
RECENCY_HALF_LIFE_DAYS: float = float(os.getenv("RECENCY_HALF_LIFE_DAYS", "14"))
RECENCY_OVERFETCH_MULTIPLIER: int = int(os.getenv("RECENCY_OVERFETCH_MULTIPLIER", "4"))
# Map of router-detected recency level -> weight applied to recency_decay term.
RECENCY_WEIGHT_MAP: dict[str, float] = {
    "high":   float(os.getenv("RECENCY_WEIGHT_HIGH",   "0.5")),  # "latest", "today" (and non-English equivalents)
    "medium": float(os.getenv("RECENCY_WEIGHT_MEDIUM", "0.3")),  # default for news/general
    "low":    float(os.getenv("RECENCY_WEIGHT_LOW",    "0.1")),  # mostly semantic
    "none":   float(os.getenv("RECENCY_WEIGHT_NONE",   "0.0")),  # historical queries
}
# TODO(future): add SOURCE_AUTHORITY_MAP (Bloomberg > Reuters > CNBC > Yahoo > Benzinga)
# and combine as: final = a*cosine + b*recency + c*authority. Park for now to avoid
# changing too many variables at once when evaluating recency rerank impact.

# ── Query expansion (multi-vector retrieval) ──
# When router returns expanded_queries (LLM-generated alt phrasings), we run
# an additional pgvector lookup per query and merge by max-cosine. This combats
# embedding bias where "latest news on AAPL" matches generic 'trending stock'
# articles instead of concrete same-day headlines like 'China iPhone +20%'.
EXPANSION_TOP_K_PER_QUERY: int = int(os.getenv("EXPANSION_TOP_K_PER_QUERY", "16"))
# Cap on number of expanded queries we'll actually use (defensive — LLM may return more)
EXPANSION_MAX_QUERIES: int = int(os.getenv("EXPANSION_MAX_QUERIES", "4"))

# ═══════════════════════════════════════════
# 5. Tier 2 cache parameters
# ═══════════════════════════════════════════
TIER2_CACHE_MAX_SIZE: int = int(os.getenv("TIER2_CACHE_MAX_SIZE", "200"))
TIER2_CACHE_TTL_PRICE: int = int(os.getenv("TIER2_CACHE_TTL_PRICE", "300"))
TIER2_CACHE_TTL_NEWS: int = int(os.getenv("TIER2_CACHE_TTL_NEWS", "3600"))
TIER2_CACHE_TTL_EARNINGS: int = int(os.getenv("TIER2_CACHE_TTL_EARNINGS", "86400"))
TIER2_CACHE_TTL_DEFAULT: int = int(os.getenv("TIER2_CACHE_TTL_DEFAULT", "1800"))

# ── Hot ticker auto-promotion threshold ──
HOT_PROMOTION_THRESHOLD: int = int(os.getenv("HOT_PROMOTION_THRESHOLD", "10"))

