"""
summarizer.py
Short on-demand ticker summaries (/api/summarize). Daily briefings live in
research.briefing.
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

def _call_llm(system_prompt: str, user_prompt: str) -> str:
    content, _usage = chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=LLM_MODEL,
        effort=LLM_EFFORT,
        service="api",
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
