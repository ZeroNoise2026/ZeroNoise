"""
skills/_llm.py
Minimal Claude (Anthropic) chat client used internally by the skills package.

Why this lives here (and not at the top of skills/) :
  Both Summarization/shared/llm.py and QuantAgent/backend/summarizer.py
  already implement near-identical Claude wrappers. The skills/ package
  is consumed by *both* of them, so we can't import either one without
  creating a cycle. This module is the neutral version — small, dependency-
  free beyond `anthropic` and `os.environ`, and intentionally not re-exported
  from skills/__init__.py (note the leading underscore).

  When we eventually consolidate the three copies (separate refactor), this
  will be the version that survives.

Reads from env:
  ANTHROPIC_API_KEY  required
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import anthropic

logger = logging.getLogger("skills._llm")

# Lazy singleton — one client per process.
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. The skills package needs it for "
                "the router and any LLM-backed skills. Check your .env."
            )
        # Retries are handled in chat(); disable SDK retries to avoid compounding.
        _client = anthropic.Anthropic(api_key=api_key, max_retries=0)
    return _client


def has_api_key() -> bool:
    """Cheap check used by the router to decide whether to even try."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def chat(
    messages: list[dict],
    *,
    model: str,
    max_tokens: int = 16000,
    effort: str = "medium",
    max_retries: int = 3,
    timeout: Optional[float] = None,
) -> tuple[str, dict]:
    """Send a Messages API request with retry on transient errors.

    `messages` uses OpenAI-style roles; "system" entries are lifted into
    Claude's top-level `system` field. Current Claude models reject
    non-default temperature, so `effort` is the only depth/latency knob.
    max_tokens includes thinking tokens — leave headroom.

    Returns (content, usage_dict). Usage dict has prompt_tokens /
    completion_tokens / total_tokens.

    Mirrors the signature of Summarization/shared/llm.py.chat so a future
    consolidation is mechanical.
    """
    client = _get_client()
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [m for m in messages if m["role"] != "system"],
        "output_config": {"effort": effort},
    }
    if system:
        kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if timeout is not None:
        kwargs["timeout"] = timeout

    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            resp = client.messages.create(**kwargs)
            if resp.stop_reason == "refusal":
                raise RuntimeError(f"{model} declined the request (stop_reason=refusal)")
            content = "".join(b.text for b in resp.content if b.type == "text")
            u = resp.usage
            prompt = (
                (u.input_tokens or 0)
                + (getattr(u, "cache_read_input_tokens", 0) or 0)
                + (getattr(u, "cache_creation_input_tokens", 0) or 0)
            )
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": u.output_tokens or 0,
                "total_tokens": prompt + (u.output_tokens or 0),
            }
            return content, usage

        except anthropic.AuthenticationError:
            # No point retrying a bad key.
            raise
        except (
            anthropic.RateLimitError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
        ) as e:
            last_err = e
            backoff = 2 ** attempt
            logger.warning(
                "Claude %s on attempt %d/%d: %s (sleeping %ds)",
                type(e).__name__, attempt + 1, max_retries, e, backoff,
            )
            time.sleep(backoff)

    assert last_err is not None  # for the type checker
    raise last_err
