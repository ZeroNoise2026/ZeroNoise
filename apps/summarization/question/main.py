"""
question/main.py
FastAPI application entry point — question-service :8003
"""

import json
import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from question.models import AskRequest
from question.orchestrator import handle_ask_stream

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="question-service",
    description="RAG-powered real-time Q&A for QuantAgent",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "question-service"}


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest):
    """SSE streaming answer."""
    async def event_generator():
        has_tokens = False
        keepalive_counter = 0
        try:
            async for item in handle_ask_stream(req):
                if item is None:
                    # Queue poll tick — send keepalive every ~2s (20 ticks * 0.1s)
                    keepalive_counter += 1
                    if keepalive_counter % 20 == 0:
                        yield ": keepalive\n\n"
                else:
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
            yield f"data: [Error: The AI model is temporarily unavailable ({type(e).__name__}). Please wait and try again.]\n\n"
        if not has_tokens:
            logger.warning("Stream completed with zero tokens")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
