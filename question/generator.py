"""
question/generator.py
Claude LLM integration — streaming (SSE).
Delegates to shared.llm for client management and retry logic.
"""

import logging
from typing import Generator

from shared.llm import chat_stream
from config import LLM_MODEL_QA, LLM_EFFORT_QA, LLM_MAX_TOKENS
from question.prompts import SYSTEM_PROMPT, build_qa_prompt

logger = logging.getLogger(__name__)


def generate_answer_stream(
    query: str,
    context: str,
    data_freshness: str = "",
) -> Generator[tuple, None, None]:
    """Generate answer in streaming mode — yields (type, text) tuples for SSE."""
    user_prompt = build_qa_prompt(query, context, data_freshness)
    total_chars = len(SYSTEM_PROMPT) + len(user_prompt)

    logger.info(f"QA stream: model={LLM_MODEL_QA}, effort={LLM_EFFORT_QA}, prompt ~{total_chars:,} chars")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    yield from chat_stream(
        messages=messages,
        model=LLM_MODEL_QA,
        effort=LLM_EFFORT_QA,
        max_tokens=LLM_MAX_TOKENS,
    )
