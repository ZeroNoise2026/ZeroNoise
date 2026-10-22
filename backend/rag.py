"""
rag.py
Proxy to question-service for RAG-based Q&A.

Instead of running its own embedding/vector-search/LLM pipeline,
this module delegates to the question-service (:8003) which handles
routing, tier1/tier2 retrieval, and LLM generation.
"""
from __future__ import annotations

import logging
import requests
from typing import Generator, Optional
from config import QUESTION_SERVICE_URL

logger = logging.getLogger(__name__)


def stream_answer_sse(
    question: str,
    ticker: Optional[str] = None,
    context_tickers: Optional[list[str]] = None,
) -> Generator[str, None, None]:
    """Proxy SSE stream from question-service — yields raw SSE lines.

    - `ticker`: explicit force-bind (from clarification chip).
    - `context_tickers`: user's watchlist; used only when no ticker in query.
    """
    url = f"{QUESTION_SERVICE_URL}/api/ask/stream"
    payload: dict = {"query": question}
    if ticker:
        payload["tickers"] = [ticker]
    if context_tickers:
        payload["context_tickers"] = [t.upper() for t in context_tickers if t]

    try:
        resp = requests.post(url, json=payload, timeout=(10, 180), stream=True)
        resp.raise_for_status()

        for line in resp.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                yield f"{line}\n\n"
                if line == "data: [DONE]":
                    return
            elif line.startswith(":"):
                # SSE keepalive comment — forward to keep browser alive
                yield f"{line}\n\n"

        # If stream ended without [DONE], send it
        yield "data: [DONE]\n\n"

    except requests.ConnectionError:
        logger.error(f"Cannot connect to question-service at {QUESTION_SERVICE_URL}")
        yield "data: [Error: Question service is not available. Please ensure it is running on port 8003.]\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.error(f"Question-service stream error: {e}")
        yield f"data: [Error: {e}]\n\n"
        yield "data: [DONE]\n\n"
