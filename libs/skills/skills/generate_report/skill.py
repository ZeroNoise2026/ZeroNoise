"""
skills/generate_report/skill.py
Full investment-analysis report for one ticker.

Thin wrapper over research.report.build_report() — the same pipeline the
summary CLI runs (same cache key, same header) — that also saves the
Markdown to REPORT_OUTPUT_DIR (default: ./output) and returns it as an
artifact.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from research.report import NoDataError, build_report
from skills.base import Skill, SkillResult

logger = logging.getLogger("skills.generate_report")


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

        try:
            report = build_report(ticker, dry_run=dry_run)
        except NoDataError as e:
            return SkillResult(content=str(e), meta={"ticker": ticker, "empty": True})

        meta: dict[str, Any] = {
            "ticker": ticker,
            "doc_counts": report.doc_counts,
            "total_chars": report.total_chars,
            "input_hash": report.input_hash,
            "cache_hit": report.cache_hit,
        }
        if dry_run:
            return SkillResult(
                content=(
                    f"[dry-run] {ticker}: {report.doc_counts}, "
                    f"{report.total_chars:,} chars, hash={report.input_hash[:12]}…"
                ),
                meta={**meta, "dry_run": True},
            )

        out_path = report.save(Path(os.getenv("REPORT_OUTPUT_DIR", "output")))
        logger.info("Report saved: %s", out_path)
        return SkillResult(content=report.markdown, artifacts=[out_path], meta=meta)
