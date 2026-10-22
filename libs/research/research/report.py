"""
research/report.py
Single-ticker investment report: prompt, Claude call, summary_cache, and the
end-to-end build_report() used by both the summary CLI and the
generate_report skill.

Model and effort come from LLM_MODEL_SUMMARY / LLM_EFFORT_SUMMARY. Reports
are offline and cached, so quality matters more than latency here.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from common.llm import chat
from research.cache import compute_input_hash, get_cached, put_cached
from research.context import TickerContext, fetch_context

logger = logging.getLogger(__name__)



def report_model() -> str:
    """Read at call time, so each app's load_dotenv() has already run."""
    return os.getenv("LLM_MODEL_SUMMARY", "claude-sonnet-5-5")

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


def generate_report(ctx: TickerContext, max_retries: int = 3) -> str:
    """Call Claude on the assembled context and return the report body (Markdown)."""
    user_prompt = build_user_prompt(ctx)
    logger.info(
        f"Calling Claude for {ctx.ticker} "
        f"(model={report_model()}, prompt ~{len(SYSTEM_PROMPT) + len(user_prompt):,} chars)"
    )
    content, usage = chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=report_model(),
        effort=os.getenv("LLM_EFFORT_SUMMARY", "high"),
        max_tokens=32000,
        max_retries=max_retries,
        service="summary",
    )
    logger.info(
        f"  {ctx.ticker} done: prompt_tokens={usage['prompt_tokens']}, "
        f"completion_tokens={usage['completion_tokens']}"
    )
    return content


class NoDataError(LookupError):
    """Supabase has nothing ingested for this ticker."""


@dataclass
class Report:
    ticker: str
    input_hash: str
    doc_counts: dict
    total_chars: int
    markdown: str = ""            # header + body; empty for a dry run
    cache_hit: bool = False
    generated_on: date = field(default_factory=date.today)

    def save(self, output_dir: Path) -> Path:
        """Write the report to <output_dir>/<TICKER>_<YYYY-MM-DD>.md."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{self.ticker}_{date.today().isoformat()}.md"
        path.write_text(self.markdown, encoding="utf-8")
        return path


def build_report(ticker: str, dry_run: bool = False) -> Report:
    """Fetch context, then reuse the cached report or generate a new one.

    Cache key: input_hash = SHA256(sorted source doc IDs + model + prompt
    version), so unchanged inputs never re-call the LLM. A dry run stops after
    computing the hash. Raises NoDataError when the ticker has no data.
    """
    ticker = ticker.strip().upper()
    ctx = fetch_context(ticker)
    if ctx.total_chars == 0:
        raise NoDataError(f"No data found for {ticker} in Supabase. Has the pipeline ingested it?")

    input_hash = compute_input_hash(ctx, report_model(), PROMPT_VERSION)
    report = Report(ticker, input_hash, ctx.doc_counts, ctx.total_chars)
    if dry_run:
        return report

    cached = get_cached(ticker, input_hash)
    if cached is not None:
        logger.info(f"Cache HIT for {ticker} (hash={input_hash[:12]}…, generated {cached.summary_date})")
        body, report.cache_hit, report.generated_on = cached.content, True, cached.summary_date
    else:
        logger.info(f"Cache MISS for {ticker} (hash={input_hash[:12]}…) — calling LLM")
        body = generate_report(ctx)
        put_cached(
            ticker=ticker,
            input_hash=input_hash,
            content=body,
            model=report_model(),
            prompt_version=PROMPT_VERSION,
            source_doc_ids=ctx.source_doc_ids,
        )

    suffix = " (cached)" if report.cache_hit else ""
    report.markdown = (
        f"# {ticker} Investment Analysis Report\n\n"
        f"> Generated on {report.generated_on.isoformat()}{suffix}\n"
        f"> Data: {ctx.doc_counts}\n\n"
        + body
    )
    return report
