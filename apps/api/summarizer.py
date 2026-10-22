"""
summarizer.py
Short on-demand ticker summaries and multi-ticker daily briefings.
"""

from common.llm import chat
from config import LLM_EFFORT, LLM_MODEL
from research.context import TickerContext

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


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    content, _usage = chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=LLM_MODEL,
        effort=LLM_EFFORT,
        service="backend",
    )
    return content


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
