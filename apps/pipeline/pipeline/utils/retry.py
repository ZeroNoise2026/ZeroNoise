"""
app/utils/retry.py
Exponential backoff retry decorator — handles upstream API transient failures (rate limits/server errors/network issues).

Usage:
    from pipeline.utils.retry import with_retry

    @with_retry(max_retries=3, base_delay=1.0)
    def call_upstream(ticker: str):
        return client.get_something(ticker)

Retryable conditions:
  - HTTP 429 (rate limited)
  - HTTP 5xx (server error: 500 / 502 / 503 / 504)
  - requests.ConnectionError / Timeout (network issues)

Immediately raised (no retry):
  - HTTP 4xx non-429 (400 Bad Request / 402 Payment / 404 Not Found etc.)
  - ValueError / KeyError and other program logic errors
"""

import functools
import logging
import random
import time
from typing import Callable, Set

import requests

logger = logging.getLogger(__name__)

# These HTTP status codes represent transient server-side failures, retry is worthwhile
RETRYABLE_HTTP_CODES: Set[int] = {429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    """Determine if the exception represents a retryable transient failure."""
    # requests-based HTTP errors (FMP, EDGAR clients)
    if isinstance(exc, requests.HTTPError):
        return exc.response.status_code in RETRYABLE_HTTP_CODES

    # Connection / timeout errors (network issues, worth retrying)
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True

    # Finnhub SDK and other upstream errors: look for status code in message
    # finnhub-python embeds status_code in str(exception)
    msg = str(exc)
    return any(str(code) in msg for code in RETRYABLE_HTTP_CODES)


def with_retry(max_retries: int = 3, base_delay: float = 1.0) -> Callable:
    """Decorator: exponential backoff retry, default max 3 retries.

    Backoff strategy: delay = base_delay * 2^attempt + uniform(0, 1.0)
      - First retry: ~1s
      - Second:      ~2s
      - Third:       ~4s

    Args:
        max_retries: Max retry attempts (excluding initial call), default 3.
        base_delay:  Initial retry wait in seconds, default 1.0s, doubles + random jitter.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    # Last attempt or non-retryable → bubble up
                    if not _is_retryable(exc) or attempt == max_retries:
                        raise
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1.0)
                    logger.warning(
                        f"{fn.__qualname__} attempt {attempt + 1}/{max_retries} "
                        f"failed ({exc!r}). Retrying in {delay:.1f}s…"
                    )
                    time.sleep(delay)
        return wrapper
    return decorator
