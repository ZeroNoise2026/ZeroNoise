"""
summary/summarizer.py
Claude API integration for generating analysis reports.
Delegates to shared.llm for client management and retry logic.

Uses LLM_MODEL_SUMMARY (Sonnet by default) at high effort — reports are
offline and cached, so quality matters more than speed here.
"""

import logging

from config import LLM_MODEL_SUMMARY, LLM_EFFORT_SUMMARY
from shared.llm import chat
from summary.prompts import SYSTEM_PROMPT, build_user_prompt
from summary.fetcher import TickerContext

logger = logging.getLogger(__name__)


def generate_summary(ctx: TickerContext, max_retries: int = 3) -> str:
    """Send context to the Claude API and return the generated analysis report."""
    user_prompt = build_user_prompt(ctx)
    total_chars = len(SYSTEM_PROMPT) + len(user_prompt)

    logger.info(
        f"Calling Claude API for {ctx.ticker} "
        f"(model={LLM_MODEL_SUMMARY}, prompt ~{total_chars:,} chars)"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    content, usage = chat(
        messages=messages,
        model=LLM_MODEL_SUMMARY,
        effort=LLM_EFFORT_SUMMARY,
        max_tokens=32000,
        max_retries=max_retries,
        service="summary",
    )
    logger.info(
        f"  {ctx.ticker} done: "
        f"model={LLM_MODEL_SUMMARY}, "
        f"prompt_tokens={usage['prompt_tokens']}, "
        f"completion_tokens={usage['completion_tokens']}, "
        f"total_tokens={usage['total_tokens']}"
    )
    return content
