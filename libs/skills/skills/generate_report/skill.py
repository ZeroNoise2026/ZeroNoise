"""
skills/generate_report/skill.py

Wrapper around the existing Summarization pipeline. We don't call
Summarization.summary.run.process_ticker directly because it returns only
a bool and we need to surface the content + artifact path to the skill
caller. Instead we re-orchestrate the same five steps it does:

  1. fetch_context        — pull docs/earnings/prices from Supabase
  2. compute_input_hash   — for cache lookup
  3. get_cached           — return cached report if inputs are unchanged
  4. generate_summary     — Claude call on cache miss
  5. put_cached + save    — persist to Supabase cache + disk

This keeps the skill in lockstep with the CLI's behavior (same cache key,
same header format) without modifying the CLI itself.

Path note: Summarization is a sibling package, not installed. We add it to
sys.path lazily so this skill works whether the caller cd's into
Summarization/ or invokes from the repo root.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from skills.base import Skill, SkillResult

logger = logging.getLogger("skills.generate_report")

# Depth note: this file is <repo-root>/libs/skills/skills/<pkg>/skill.py, so
# parents[4] is the monorepo root. Update if this file ever moves.
_SUMMARIZATION_DIR = Path(__file__).resolve().parents[4] / "apps" / "summarization"


def _ensure_summarization_importable() -> None:
    """Add Summarization/ to sys.path so its top-level modules (config,
    shared, summary) resolve. Idempotent."""
    p = str(_SUMMARIZATION_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


class GenerateReportSkill(Skill):
    name = "generate_report"
    description = (
        "Generate a full investment-analysis report (markdown) for one ticker. "
        "Use for in-depth single-ticker analysis. For multi-ticker comparison, "
        "use compare_tickers instead."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": "Ticker symbol, e.g. AAPL.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "If true, fetch context only; skip the LLM call.",
                "default": False,
            },
        },
        "required": ["ticker"],
    }

    def run(self, *, ticker: str, dry_run: bool = False) -> SkillResult:
        ticker = ticker.strip().upper()
        if not ticker:
            raise ValueError("ticker must be a non-empty string")

        _ensure_summarization_importable()
        # Imports are local because they require sys.path to be set up first
        # and we don't want to fail at import time of this skill module just
        # because Summarization isn't on PYTHONPATH yet.
        from config import LLM_MODEL_SUMMARY, OUTPUT_DIR  # noqa: E402
        from summary.cache import compute_input_hash, get_cached, put_cached  # noqa: E402
        from summary.fetcher import fetch_context  # noqa: E402
        from summary.prompts import PROMPT_VERSION  # noqa: E402
        from summary.summarizer import generate_summary  # noqa: E402

        # 1) Fetch
        ctx = fetch_context(ticker)
        if ctx.total_chars == 0:
            return SkillResult(
                content=f"No data found for {ticker} in Supabase. "
                        "Has the data-pipeline ingested it yet?",
                meta={"ticker": ticker, "empty": True},
            )

        input_hash = compute_input_hash(ctx, LLM_MODEL_SUMMARY, PROMPT_VERSION)
        meta: dict[str, Any] = {
            "ticker": ticker,
            "doc_counts": ctx.doc_counts,
            "total_chars": ctx.total_chars,
            "input_hash": input_hash,
            "cache_hit": False,
        }

        if dry_run:
            return SkillResult(
                content=(
                    f"[dry-run] {ticker}: {ctx.doc_counts}, "
                    f"{ctx.total_chars:,} chars, hash={input_hash[:12]}…"
                ),
                meta={**meta, "dry_run": True},
            )

        # 2) Cache lookup
        cached = get_cached(ticker, input_hash)
        if cached is not None:
            logger.info(
                "Cache HIT for %s (hash=%s…)", ticker, input_hash[:12]
            )
            report = cached.content
            header = (
                f"# {ticker} Investment Analysis Report\n\n"
                f"> Generated on {cached.summary_date.isoformat()} (cached)\n"
                f"> Data: {ctx.doc_counts}\n\n"
            )
            meta["cache_hit"] = True
            meta["cached_date"] = cached.summary_date.isoformat()
        else:
            # 3) LLM
            logger.info(
                "Cache MISS for %s (hash=%s…) — calling LLM",
                ticker, input_hash[:12]
            )
            report = generate_summary(ctx)
            # 4) Write back to cache (best-effort)
            try:
                put_cached(
                    ticker=ticker,
                    input_hash=input_hash,
                    content=report,
                    model=LLM_MODEL_SUMMARY,
                    prompt_version=PROMPT_VERSION,
                    source_doc_ids=ctx.source_doc_ids,
                )
            except Exception as e:
                # Match CLI behavior: cache write failure isn't fatal.
                logger.warning("Cache write failed for %s: %s", ticker, e)
            header = (
                f"# {ticker} Investment Analysis Report\n\n"
                f"> Generated on {date.today().isoformat()}\n"
                f"> Data: {ctx.doc_counts}\n\n"
            )

        # 5) Save
        full_content = header + report
        out_path = Path(OUTPUT_DIR) / f"{ticker}_{date.today().isoformat()}.md"
        out_path.write_text(full_content, encoding="utf-8")
        logger.info("Report saved: %s", out_path)

        return SkillResult(
            content=full_content,
            artifacts=[out_path],
            meta=meta,
        )
