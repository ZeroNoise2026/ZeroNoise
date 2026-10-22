"""
question/sse.py
Run the question flow and render it as Server-Sent Events.

Events: `data: {"type": <event>, "text": ...}` for status/token/thinking/
clarification, `data: {"type": "sources", "items": [...]}` for citations,
`: keepalive` comments every ~2s while the model is busy, and a final
`data: [DONE]`.
"""

import json
import logging
from typing import AsyncIterator

from question.models import AskRequest
from question.orchestrator import handle_ask_stream

logger = logging.getLogger(__name__)

# handle_ask_stream yields None on each ~0.1s queue poll; 20 ticks ≈ 2s.
_KEEPALIVE_EVERY = 20


async def ask_sse(req: AskRequest) -> AsyncIterator[str]:
    """Yield SSE lines for one question."""
    has_tokens = False
    ticks = 0
    try:
        async for item in handle_ask_stream(req):
            if item is None:
                ticks += 1
                if ticks % _KEEPALIVE_EVERY == 0:
                    yield ": keepalive\n\n"
                continue
            event_type, payload = item
            if event_type == "token":
                has_tokens = True
            if event_type == "sources":
                # payload is already a list[dict]; keep it under "items" so
                # frontends can disambiguate from text-bearing events.
                yield f"data: {json.dumps({'type': 'sources', 'items': payload})}\n\n"
            else:
                yield f"data: {json.dumps({'type': event_type, 'text': payload})}\n\n"
    except Exception as e:
        logger.error(f"Stream generation error: {type(e).__name__}: {e}")
        yield (
            "data: [Error: The AI model is temporarily unavailable "
            f"({type(e).__name__}). Please wait and try again.]\n\n"
        )
    if not has_tokens:
        logger.warning("Stream completed with zero tokens")
    yield "data: [DONE]\n\n"
