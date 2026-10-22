"""
skills/compare_tickers/skill.py

Multi-ticker comparison. For each ticker we call the same Summarization
fetch_context() the report skill uses (so the data is identical), then
assemble a single prompt that asks the LLM for a side-by-side comparison
table + qualitative read.

Design choice: we don't reuse the report-generation prompt — that one
produces a *standalone* deep-dive per ticker, which is the wrong shape
here. Comparison needs concise per-ticker summaries plus an explicit
"who's better at X" verdict, so we ship a dedicated system prompt.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from skills._llm import chat
from skills.base import Skill, SkillResult

logger = logging.getLogger("skills.compare_tickers")

# Depth note: this file is <project-root>/Skills/skills/<pkg>/skill.py, so
# parents[3] is the project root holding both the Skills clone and the
# sibling Summarization repo. Update if this file ever moves.
_SUMMARIZATION_DIR = Path(__file__).resolve().parents[3] / "Summarization"

# Per-ticker character budget. Three tickers × 80k ≈ 240k chars ≈ ~60k
# tokens — well inside the 1M context and under Haiku's 100k-token
# price tier. Raising it past ~130k per ticker crosses that tier.
_PER_TICKER_BUDGET = 80_000
_COMPARE_MODEL = os.environ.get("SKILLS_COMPARE_MODEL", "claude-haiku-5-5")


_SYSTEM_PROMPT = """\
You are a senior equity research analyst writing concise comparison memos.

Given the data blocks for several tickers, produce a Markdown report with
EXACTLY this structure:

1. **One-line take** — single sentence verdict.
2. **Metrics table** — a Markdown table comparing key figures (latest
   price, P/E if available, latest quarter revenue + YoY growth, EPS,
   recent catalysts in one phrase). One row per ticker.
3. **Per-ticker observations** — 2-3 bullet points per ticker covering
   what stands out from the news + filings + earnings.
4. **Verdict** — explicitly answer the user's comparison angle (the
   "Focus" field below). If they didn't specify one, default to "overall
   fundamentals + near-term setup". Rank the tickers and justify.

Rules:
- Use only the data provided. If a metric is missing for one ticker,
  write "n/a" rather than inventing a number.
- Keep the whole report under 800 words — this is a comparison memo,
  not a deep-dive.
- Write in the same language the user's request is in. If unsure,
  default to English."""


def _ensure_summarization_importable() -> None:
    p = str(_SUMMARIZATION_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _truncate(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget] + "\n\n…[truncated]"


def _format_ticker_block(ctx: Any, budget: int) -> str:
    """Render a TickerContext into a labeled text block for the prompt.

    We don't reuse summary.prompts.build_user_prompt because that builds a
    full single-ticker user prompt; here we just want a compact chunk we
    can repeat for N tickers.
    """
    parts = [f"### Ticker: {ctx.ticker}"]
    if ctx.price_text:
        parts.append("**Price / Snapshot**\n" + ctx.price_text)
    if ctx.earnings_text:
        parts.append("**Earnings**\n" + ctx.earnings_text)
    if ctx.regulatory_text:
        parts.append("**Regulatory**\n" + ctx.regulatory_text)
    if ctx.filings_text:
        parts.append("**Filings**\n" + ctx.filings_text)
    if ctx.news_text:
        parts.append("**News**\n" + ctx.news_text)
    block = "\n\n".join(parts)
    return _truncate(block, budget)


class CompareTickersSkill(Skill):
    name = "compare_tickers"
    description = (
        "Side-by-side comparison of 2 or more tickers — metrics table, "
        "per-ticker observations, and a ranked verdict. Use when the user "
        "explicitly wants to compare or pick among multiple tickers."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": "Ticker symbols to compare, e.g. ['AAPL','MSFT'].",
            },
            "focus": {
                "type": "string",
                "description": "Comparison angle (e.g. 'valuation', "
                               "'earnings growth'). Optional.",
            },
        },
        "required": ["tickers"],
    }

    def run(
        self,
        *,
        tickers: list[str],
        focus: str | None = None,
    ) -> SkillResult:
        if not isinstance(tickers, list) or len(tickers) < 2:
            raise ValueError("compare_tickers needs at least 2 tickers")
        tickers = [t.strip().upper() for t in tickers if t and t.strip()]
        if len(tickers) < 2:
            raise ValueError("compare_tickers needs at least 2 valid tickers")

        _ensure_summarization_importable()
        from summary.fetcher import fetch_context  # noqa: E402

        blocks: list[str] = []
        ticker_chars: dict[str, int] = {}
        missing: list[str] = []
        for t in tickers:
            ctx = fetch_context(t)
            if ctx.total_chars == 0:
                missing.append(t)
                continue
            block = _format_ticker_block(ctx, _PER_TICKER_BUDGET)
            ticker_chars[t] = len(block)
            blocks.append(block)

        if not blocks:
            return SkillResult(
                content=(
                    "No data found in Supabase for any of the requested "
                    f"tickers: {', '.join(tickers)}. Has the data-pipeline "
                    "ingested them?"
                ),
                meta={"tickers": tickers, "empty": True},
            )

        user_prompt_parts = [
            f"Compare these {len(blocks)} tickers: "
            f"{', '.join(t for t in tickers if t not in missing)}.",
            f"Focus: {focus}" if focus else "Focus: overall fundamentals + near-term setup.",
        ]
        if missing:
            user_prompt_parts.append(
                f"(No data available for: {', '.join(missing)} — exclude them.)"
            )
        user_prompt_parts.append("---")
        user_prompt_parts.extend(blocks)
        user_prompt = "\n\n".join(user_prompt_parts)

        logger.info(
            "compare_tickers calling %s (%d tickers, %d chars)",
            _COMPARE_MODEL, len(blocks), sum(ticker_chars.values()),
        )
        content, usage = chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=_COMPARE_MODEL,
        )

        return SkillResult(
            content=content,
            meta={
                "tickers": tickers,
                "focus": focus,
                "ticker_chars": ticker_chars,
                "missing": missing,
                "usage": usage,
            },
        )
