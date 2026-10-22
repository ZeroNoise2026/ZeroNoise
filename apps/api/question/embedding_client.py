"""
question/embedding_client.py
Async HTTP client for the embedding service — created on first use, reused
globally.
"""

import logging
from typing import Optional

import httpx

from config import EMBEDDING_SERVICE_URL

logger = logging.getLogger(__name__)

_embedding_client: Optional[httpx.AsyncClient] = None


def get_embedding_client() -> httpx.AsyncClient:
    """Singleton async client for the embedding service (:8002)."""
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = httpx.AsyncClient(
            base_url=EMBEDDING_SERVICE_URL,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
        )
        logger.debug(f"Created embedding client → {EMBEDDING_SERVICE_URL}")
    return _embedding_client

