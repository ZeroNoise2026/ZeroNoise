"""
shared/http.py
Shared async HTTP client singletons — one per upstream service.

Usage:
    from shared.http import get_embedding_client, get_pipeline_client

Clients are lazy-created on first call and reused globally.
"""

import logging
from typing import Optional

import httpx

from config import EMBEDDING_SERVICE_URL, DATA_PIPELINE_URL

logger = logging.getLogger(__name__)

_embedding_client: Optional[httpx.AsyncClient] = None
_pipeline_client: Optional[httpx.AsyncClient] = None


def get_embedding_client() -> httpx.AsyncClient:
    """Singleton async client for embedding-service :8002."""
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = httpx.AsyncClient(
            base_url=EMBEDDING_SERVICE_URL,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
        )
        logger.debug(f"Created embedding-service client → {EMBEDDING_SERVICE_URL}")
    return _embedding_client


def get_pipeline_client() -> httpx.AsyncClient:
    """Singleton async client for data-pipeline :8001."""
    global _pipeline_client
    if _pipeline_client is None:
        _pipeline_client = httpx.AsyncClient(
            base_url=DATA_PIPELINE_URL,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        )
        logger.debug(f"Created data-pipeline client → {DATA_PIPELINE_URL}")
    return _pipeline_client


async def close_all() -> None:
    """Shutdown hook — close all HTTP clients gracefully."""
    global _embedding_client, _pipeline_client
    if _embedding_client:
        await _embedding_client.aclose()
        _embedding_client = None
    if _pipeline_client:
        await _pipeline_client.aclose()
        _pipeline_client = None
    logger.debug("All HTTP clients closed")
