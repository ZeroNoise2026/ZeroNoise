"""
summary/prompts.py
Prompt templates for generating investment analysis reports via Claude.
"""

from summary.fetcher import TickerContext

# Bump this when SYSTEM_PROMPT or build_user_prompt changes in a way that
# should invalidate the summary_cache (e.g., new section, format change).
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are a senior financial analyst skilled at synthesizing multi-source financial data into concise investment summaries.
Your summary must meet the following requirements:
1. STRICT 400-word limit — be concise and prioritize the most important information
2. Well-structured, using Markdown format
3. All data citations must be specific (dates, numbers) — never fabricate data
4. If a category of data is missing, skip it — do not invent information

Cover these areas briefly:
- Company overview & recent developments
- Stock price & valuation
- Financial highlights (revenue, EPS)
- Regulatory & compliance events (lawsuits, SEC actions, FDA decisions, fines, investigations — if any)
- Key news & risks
- Outlook

For the regulatory section: only include it when regulatory/compliance data is provided. Highlight severity (material vs. routine), dates, and potential financial impact."""


def build_user_prompt(ctx: TickerContext) -> str:
    """Assemble all context into a single user prompt for the LLM."""
    sections = [f"Based on the following data, generate a comprehensive investment analysis report for **{ctx.ticker}**.\n"]

    if ctx.price_text:
        sections.append(f"### Recent Price Data\n\n{ctx.price_text}")
    if ctx.earnings_text:
        sections.append(f"### Earnings Data\n\n{ctx.earnings_text}")
    if ctx.news_text:
        sections.append(f"### News & Developments\n\n{ctx.news_text}")
    if ctx.regulatory_text:
        sections.append(f"### Regulatory & Compliance Events\n\n{ctx.regulatory_text}")
    if ctx.filings_text:
        sections.append(f"### SEC Filings / Financial Report Content\n\n{ctx.filings_text}")
    if not any([ctx.price_text, ctx.earnings_text, ctx.news_text, ctx.regulatory_text, ctx.filings_text]):
        sections.append("(No data available)")

    return "\n\n".join(sections)
