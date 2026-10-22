"""
question/tier2_cache.py
Tier 2 in-memory cache — TTL + LRU eviction.

Per-instance by design (it only saves live-API calls). Hot-ticker promotion
needs a cross-instance tally, so it lives in Postgres — see
orchestrator._maybe_promote.
"""

import logging
import time
from collections import OrderedDict
from typing import Optional

from config import (
    TIER2_CACHE_MAX_SIZE,
    TIER2_CACHE_TTL_PRICE,
    TIER2_CACHE_TTL_NEWS,
    TIER2_CACHE_TTL_EARNINGS,
    TIER2_CACHE_TTL_DEFAULT,
)
from question.models import Intent

logger = logging.getLogger(__name__)


def _ttl_for_intent(intent: Intent) -> int:
    """Return cache TTL in seconds based on intent type."""
    mapping = {
        Intent.PRICE_QUERY: TIER2_CACHE_TTL_PRICE,          # 5 min (prices change fast)
        Intent.NEWS_SUMMARY: TIER2_CACHE_TTL_NEWS,           # 1 hour
        Intent.EARNINGS_ANALYSIS: TIER2_CACHE_TTL_EARNINGS,  # 24 hours
    }
    return mapping.get(intent, TIER2_CACHE_TTL_DEFAULT)      # default 30 min


class Tier2Cache:
    """
    In-memory cache: key = "TICKER:intent" -> value = (context_str, expire_time)
    Eviction: TTL expiry + LRU (when exceeding MAX_SIZE, oldest entry removed)
    """

    def __init__(self, max_size: int = TIER2_CACHE_MAX_SIZE):
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._max_size = max_size

    def _make_key(self, ticker: str, intent: Intent) -> str:
        return f"{ticker.upper()}:{intent.value}"

    def get(self, ticker: str, intent: Intent) -> Optional[str]:
        """Look up cache. Returns None on miss or expiry."""
        key = self._make_key(ticker, intent)
        entry = self._cache.get(key)
        if entry is None:
            return None

        context, expire_time = entry
        if time.time() > expire_time:
            # TTL expired
            del self._cache[key]
            return None

        # LRU: move to end (most recently used)
        self._cache.move_to_end(key)
        return context

    def put(self, ticker: str, intent: Intent, context: str) -> None:
        """Write to cache."""
        key = self._make_key(ticker, intent)
        ttl = _ttl_for_intent(intent)
        expire_time = time.time() + ttl

        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (context, expire_time)

        # LRU eviction
        while len(self._cache) > self._max_size:
            evicted_key, _ = self._cache.popitem(last=False)
            logger.debug(f"Cache evicted: {evicted_key}")

    def clear(self) -> None:
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


# Module-level singleton
cache = Tier2Cache()
