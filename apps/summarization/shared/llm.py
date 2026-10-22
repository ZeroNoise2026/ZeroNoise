"""
shared/llm.py
Shared Claude (Anthropic) LLM client — singleton + retry logic.

Used by:
  - summary/summarizer.py   (report generation, LLM_MODEL_SUMMARY)
  - question/generator.py   (QA generation, LLM_MODEL_QA, streaming)
  - question/router.py      (L2 classification, LLM_MODEL_CLASSIFY)
  - question/structured.py  (JSON generation, LLM_MODEL_QA)

Callers pass OpenAI-style messages ([{"role": "system"|"user"|"assistant", ...}]);
split_messages() lifts system turns into Claude's top-level `system` field.

Note: current Claude models reject non-default temperature, so there is no
temperature knob — use `effort` ("low" | "medium" | "high") to trade depth
for latency. Thinking tokens count against max_tokens, so leave headroom.
"""

import logging
import time
from typing import Any, Generator, Optional

import anthropic

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

# Models that support server-side refusal fallback (`fallbacks: "default"`).
# Haiku has no server-side fallback — refusals surface as LLMRefusal instead.
_FALLBACK_MODEL_PREFIXES = ("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_REFUSAL_TEXT = "[The AI model declined to answer this question. Please rephrase and try again.]"


class LLMRefusal(RuntimeError):
    """Claude declined the request (stop_reason == "refusal")."""


_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    """Lazy singleton — one Anthropic client shared across the entire process."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set. Check your .env file.")
        # Retries are handled below so they can be audited; disable SDK retries
        # to avoid compounding attempts.
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)
    return _client


def has_api_key() -> bool:
    """Check if ANTHROPIC_API_KEY is configured (without raising)."""
    return bool(ANTHROPIC_API_KEY)


def split_messages(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
    """Split OpenAI-style messages into (system_prompt, user/assistant turns)."""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    return ("\n\n".join(system_parts) or None), turns


def build_request(
    messages: list[dict],
    model: str,
    max_tokens: int,
    effort: str,
    output_schema: Optional[dict] = None,
    show_thinking: bool = False,
) -> tuple[Any, dict]:
    """Build (messages_resource, kwargs) for a Messages API call.

    Returns the beta resource when the model supports server-side refusal
    fallback, so callers can do `resource.create(**kwargs)` / `.stream(**kwargs)`.
    """
    client = get_client()
    system, turns = split_messages(messages)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": turns,
        "output_config": {"effort": effort},
    }
    if system:
        # System prompts are stable per call site — cache them.
        kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if output_schema is not None:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": output_schema}
    if show_thinking:
        kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}

    if model.startswith(_FALLBACK_MODEL_PREFIXES):
        kwargs["betas"] = [_FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
        return client.beta.messages, kwargs
    return client.messages, kwargs


def usage_dict(usage: Any) -> dict:
    """Normalize Claude usage into the prompt/completion/total shape callers expect."""
    prompt = (
        (usage.input_tokens or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    completion = usage.output_tokens or 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def response_text(response: Any) -> str:
    """Concatenate the text blocks of a response (skips thinking/fallback blocks)."""
    return "".join(b.text for b in response.content if b.type == "text")


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, (anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    return isinstance(e, anthropic.APIStatusError) and e.status_code >= 500


def _backoff(e: Exception, attempt: int) -> int:
    if isinstance(e, anthropic.RateLimitError):
        return min(2 ** attempt * 10, 60)
    return 2 ** attempt * 5


def chat(
    messages: list[dict],
    model: str,
    max_tokens: int = 16000,
    effort: str = "medium",
    output_schema: Optional[dict] = None,
    max_retries: int = 3,
    timeout: Optional[float] = None,
    service: str = "question",
) -> tuple[str, dict]:
    """Send a Messages API request with retry logic.

    Args:
        messages: OpenAI-style messages [{"role": ..., "content": ...}]
        model: Claude model ID (e.g. "claude-haiku-5-5")
        max_tokens: Max output tokens, thinking included
        effort: "low" | "medium" | "high" — thinking depth / latency trade-off
        output_schema: JSON schema to constrain the response (structured outputs)
        max_retries: Number of attempts for rate-limit / connection / 5xx errors
        timeout: Request timeout in seconds (None = client default)
        service: Audit-log service label

    Returns:
        (content, usage_dict) where usage_dict has prompt_tokens, completion_tokens, total_tokens

    Raises:
        LLMRefusal if Claude declines the request.
    """
    resource, kwargs = build_request(messages, model, max_tokens, effort, output_schema)
    if timeout is not None:
        kwargs["timeout"] = timeout

    from audit import log_api_call

    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            response = resource.create(**kwargs)
            latency = int((time.time() - t0) * 1000)
            usage = usage_dict(response.usage)
            logger.info(
                f"LLM call done: model={response.model}, stop={response.stop_reason}, "
                f"prompt={usage['prompt_tokens']} (cached={usage['cache_read_tokens']}), "
                f"completion={usage['completion_tokens']}"
            )
            log_api_call(
                service=service,
                api_type="claude_llm",
                endpoint="messages",
                model=response.model,
                tokens_in=usage["prompt_tokens"],
                tokens_out=usage["completion_tokens"],
                tokens_total=usage["total_tokens"],
                latency_ms=latency,
            )
            if response.stop_reason == "refusal":
                raise LLMRefusal(f"model={response.model} declined the request")
            if response.stop_reason == "max_tokens":
                logger.warning(f"LLM output truncated at max_tokens={max_tokens} (model={model})")
            return response_text(response), usage

        except anthropic.AuthenticationError as e:
            # Invalid/placeholder API key — don't retry, just raise
            logger.warning(f"LLM authentication failed (invalid API key): {e}")
            raise

        except Exception as e:
            if not _is_retryable(e) or attempt == max_retries:
                if _is_retryable(e):
                    log_api_call(
                        service=service, api_type="claude_llm", endpoint="messages", model=model,
                        latency_ms=int((time.time() - t0) * 1000),
                        status="rate_limited" if isinstance(e, anthropic.RateLimitError) else "error",
                        error_msg=str(e)[:200],
                    )
                    break
                raise
            wait = _backoff(e, attempt)
            logger.warning(f"{type(e).__name__} (attempt {attempt}/{max_retries}), retrying in {wait}s: {e}")
            time.sleep(wait)

    raise RuntimeError(f"LLM call failed after {max_retries} attempts (model={model})")


def chat_stream(
    messages: list[dict],
    model: str,
    max_tokens: int = 16000,
    effort: str = "medium",
    show_thinking: bool = True,
    max_retries: int = 3,
) -> Generator[tuple, None, None]:
    """Send a streaming Messages API request — yields (type, text) tuples.

    Tuple types:
      ("thinking", text) — summarized reasoning (when show_thinking=True)
      ("token", text)    — final answer content tokens

    Retries on transient errors only while nothing has been yielded yet, so a
    mid-stream failure never duplicates output. Errors are yielded as a final
    token instead of raised — avoids async/sync generator exception
    propagation issues with ASGI.
    """
    resource, kwargs = build_request(messages, model, max_tokens, effort, show_thinking=show_thinking)

    from audit import log_api_call

    for attempt in range(1, max_retries + 1):
        emitted = False
        t0 = time.time()
        try:
            with resource.stream(**kwargs) as stream:
                for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    if event.delta.type == "thinking_delta" and event.delta.thinking:
                        emitted = True
                        yield ("thinking", event.delta.thinking)
                    elif event.delta.type == "text_delta" and event.delta.text:
                        emitted = True
                        yield ("token", event.delta.text)
                final = stream.get_final_message()

            usage = usage_dict(final.usage)
            log_api_call(
                service="question",
                api_type="claude_llm",
                endpoint="messages.stream",
                model=final.model,
                tokens_in=usage["prompt_tokens"],
                tokens_out=usage["completion_tokens"],
                tokens_total=usage["total_tokens"],
                latency_ms=int((time.time() - t0) * 1000),
            )
            if final.stop_reason == "refusal":
                yield ("token", "\n\n" + _REFUSAL_TEXT if emitted else _REFUSAL_TEXT)
            return  # success — exit retry loop

        except Exception as e:
            logger.warning(f"Stream {type(e).__name__} (attempt {attempt}/{max_retries}): {e}")
            if isinstance(e, anthropic.RateLimitError):
                log_api_call(
                    service="question",
                    api_type="claude_llm", endpoint="messages.stream", model=model,
                    status="rate_limited", error_msg=str(e)[:200],
                )
            if emitted or not _is_retryable(e) or attempt == max_retries:
                if isinstance(e, anthropic.RateLimitError):
                    yield ("token", "[Error: The AI model is rate-limited. Please wait a moment and try again.]")
                else:
                    yield ("token", "[Error: Connection to AI model failed. Please try again.]")
                return
            time.sleep(_backoff(e, attempt))
