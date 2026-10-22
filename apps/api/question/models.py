"""
question/models.py
All API request / response / internal data structures.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class QueryMode(str, Enum):
    """Query mode — Router output, determines which retrieval path to use."""
    SEMANTIC   = "semantic"
    STRUCTURED = "structured"
    HYBRID     = "hybrid"


class Intent(str, Enum):
    """User intent — identified by Router, affects prompt and data strategy."""
    NEWS_SUMMARY      = "news_summary"
    EARNINGS_ANALYSIS = "earnings_analysis"
    PRICE_QUERY       = "price_query"
    TRADE_SUGGESTION  = "trade_suggestion"
    COMPARISON        = "comparison"
    GENERAL_ANALYSIS  = "general_analysis"


class Recency(str, Enum):
    """How strongly the user wants fresh data — controls retriever's recency weight.

    high   -> 'latest', 'today', 'recent' (and non-English equivalents) — news-leaning
    medium -> default for most queries
    low    -> educational / explanation queries
    none   -> historical: 'last year', 'during COVID' (and non-English equivalents) — pure semantic
    """
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"
    NONE   = "none"


class RouterResult(BaseModel):
    tickers: List[str] = Field(default_factory=list, description="Extracted ticker symbols")
    intent: Intent = Field(default=Intent.GENERAL_ANALYSIS)
    mode: QueryMode = Field(default=QueryMode.HYBRID)
    recency: Recency = Field(default=Recency.MEDIUM, description="Freshness preference for reranking")
    time_window_days: int = Field(default=0, description="Optional hard time window (0 = no filter); soft-weighted by default")
    quarter: Optional[str] = Field(
        default=None,
        description="Explicit quarter mentioned in query, normalized to 'Q3 2025' format. None = use latest available.",
    )
    expanded_queries: List[str] = Field(
        default_factory=list,
        description=(
            "LLM-generated alternative phrasings of the user query, used for "
            "multi-vector retrieval to overcome embedding bias. Empty = no expansion."
        ),
    )
    # ── Ticker-less query fallback (Plan D) ──
    needs_clarification: bool = Field(
        default=False,
        description="True when no ticker extracted and context_tickers has >=2 options — UI should show chips.",
    )
    clarification_options: List[str] = Field(
        default_factory=list,
        description="Candidate tickers to show as chips when needs_clarification=True.",
    )
    auto_bound_ticker: Optional[str] = Field(
        default=None,
        description="Ticker auto-bound from watchlist (when context_tickers has exactly 1). LLM note appended.",
    )


class Source(BaseModel):
    """Tracks where a piece of retrieved context came from.

    Two flavors share the same shape so the frontend renders them uniformly:
      - Document-derived (id="doc:<sha>"): chunked news/filing/earnings_doc.
        Has title; may have url.
      - Table-derived  (id="table:<kind>:<ticker>:<key>"): synthesized from
        structured tables. Has label; never has url.

    Render rules (frontend):
      - url present                 -> clickable anchor
      - title present, no url       -> grey "<title> (no link)"
      - else                        -> grey label
    """
    id: str = Field(..., description="Stable identifier; doc:<sha> or table:<kind>:<key>")
    doc_type: str
    ticker: str
    date: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    label: Optional[str] = None
    similarity: Optional[float] = None


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="User question")
    user_id: str = Field(default="anonymous")
    tickers: List[str] = Field(default_factory=list, description="Explicitly specified tickers (force-bind, e.g. from clarification chip)")
    context_tickers: List[str] = Field(
        default_factory=list,
        description="User's watchlist — used as fallback scope only when query has no ticker.",
    )
