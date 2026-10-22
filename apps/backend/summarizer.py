"""
summarizer.py
Claude API client for on-demand summarization and daily briefing generation.
"""

import logging
import time

import anthropic

from config import ANTHROPIC_API_KEY, LLM_MODEL, LLM_EFFORT
from fetcher import TickerContext

logger = logging.getLogger(__name__)
_client = None

SUMMARY_SYSTEM_PROMPT = """You are a senior financial analyst skilled at synthesizing multi-source financial data into concise investment summaries.

Your summary must meet the following requirements:
1. STRICT 300-word limit — be concise and prioritize the most important information
2. Well-structured, using Markdown format
3. All data citations must be specific (dates, numbers) — never fabricate data
4. If a category of data is missing, skip it — do not invent information

Cover these areas briefly:
- Company overview & recent developments
- Stock price & valuation
- Financial highlights (revenue, EPS)
- Key news & risks
- Outlook"""

BRIEFING_SYSTEM_PROMPT = """You are a senior financial analyst. Generate a concise daily briefing for the user's watchlist.

Requirements:
1. For each ticker, write a short paragraph (80-120 words) covering:
   - Current price & recent move
   - Key news (if any)
   - Any notable financial data
2. Use Markdown with ## headers for each ticker
3. Be factual — cite specific numbers and dates
4. End with a brief 2-sentence overall market outlook
5. If no meaningful data exists for a ticker, note it briefly and move on"""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        # Retries are handled in _call_llm; disable SDK retries to avoid compounding.
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)
    return _client


def _call_llm(system_prompt: str, user_prompt: str, max_retries: int = 3) -> str:
    client = _get_client()
    total_chars = len(system_prompt) + len(user_prompt)
    model = LLM_MODEL

    logger.info(f"  LLM call: model={model}, prompt ~{total_chars:,} chars")

    for attempt in range(1, max_retries + 1):
        try:
            # Current Claude models reject non-default temperature; effort is the
            # depth knob. max_tokens includes thinking tokens.
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_prompt}],
                output_config={"effort": LLM_EFFORT},
            )
            usage = response.usage
            logger.info(f"  LLM done: model={model}, prompt={usage.input_tokens}, completion={usage.output_tokens}")
            if response.stop_reason == "refusal":
                raise RuntimeError(f"{model} declined the request (stop_reason=refusal)")
            return "".join(b.text for b in response.content if b.type == "text")

        except anthropic.RateLimitError as e:
            wait = min(2 ** attempt * 10, 60)
            logger.warning(f"  Rate limited (attempt {attempt}/{max_retries}), waiting {wait}s: {e}")
            time.sleep(wait)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            wait = 2 ** attempt * 5
            logger.warning(f"  Connection issue (attempt {attempt}/{max_retries}), retrying in {wait}s: {e}")
            time.sleep(wait)
        except Exception as e:
            logger.error(f"  Unexpected error: {e}")
            raise

    raise RuntimeError(f"LLM call failed after {max_retries} attempts")


def generate_summary(ctx: TickerContext) -> str:
    """Generate a single-ticker investment summary."""
    sections = [f"Based on the following data, generate a concise investment summary for **{ctx.ticker}**.\n"]
    if ctx.price_text:
        sections.append(f"### Recent Price Data\n\n{ctx.price_text}")
    if ctx.earnings_text:
        sections.append(f"### Earnings Data\n\n{ctx.earnings_text}")
    if ctx.news_text:
        sections.append(f"### News & Developments\n\n{ctx.news_text}")
    if ctx.filings_text:
        sections.append(f"### SEC Filings / Financial Report Content\n\n{ctx.filings_text}")
    user_prompt = "\n\n".join(sections)

    return _call_llm(SUMMARY_SYSTEM_PROMPT, user_prompt)


def generate_briefing(contexts: list[TickerContext]) -> str:
    """Generate a multi-ticker daily briefing."""
    sections = ["Generate a daily investment briefing for the following watchlist tickers.\n"]
    for ctx in contexts:
        parts = [f"### {ctx.ticker}"]
        if ctx.price_text:
            parts.append(f"Price data:\n{ctx.price_text}")
        if ctx.earnings_text:
            parts.append(f"Earnings:\n{ctx.earnings_text}")
        if ctx.news_text:
            parts.append(f"Recent news:\n{ctx.news_text[:3000]}")
        if not ctx.price_text and not ctx.news_text:
            parts.append("(Limited data available)")
        sections.append("\n".join(parts))
    user_prompt = "\n\n---\n\n".join(sections)

    return _call_llm(BRIEFING_SYSTEM_PROMPT, user_prompt)
