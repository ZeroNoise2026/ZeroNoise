"""
summary/run.py
Main entry point for the Summarization service.

Usage:
    python -m summary.run --ticker AAPL
    python -m summary.run --ticker AAPL,MSFT,NVDA
    python -m summary.run --all
    python -m summary.run --ticker AAPL --dry-run
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from config import OUTPUT_DIR, LLM_MODEL_SUMMARY
from summary.fetcher import fetch_context
from summary.summarizer import generate_summary
from summary.prompts import PROMPT_VERSION
from summary.cache import compute_input_hash, get_cached, put_cached
from shared.db import get_tracked_tickers

# Make the shared `skills` package importable.
#
# The package lives at <project-root>/<clone>/skills/, where <clone> is
# whatever `git clone .../Skills.git` produced — "Skills" by default, which
# does NOT match `import skills` on a case-sensitive filesystem (Linux,
# Docker, CI). So probe the candidate clone names instead of assuming one.
#
# If no candidate matches, the package was pip-installed instead (see
# Skills/pyproject.toml) and the installed distribution is used — that is
# how the container image gets it.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _clone_name in ("Skills", "skills"):
    _skills_repo = _PROJECT_ROOT / _clone_name
    if (_skills_repo / "skills" / "__init__.py").is_file():
        if str(_skills_repo) not in sys.path:
            sys.path.insert(0, str(_skills_repo))
        break

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run")


def save_report(ticker: str, content: str) -> Path:
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{ticker}_{today}.md"
    path.write_text(content, encoding="utf-8")
    return path


def process_ticker(ticker: str, dry_run: bool = False) -> bool:
    """Fetch data, generate summary, and save report. Returns True on success.

    Caching: keyed on input_hash = SHA256(sorted source doc IDs + model +
    prompt_version). Identical inputs reuse the cached summary instead of
    re-calling the LLM.
    """
    try:
        ctx = fetch_context(ticker)

        if ctx.total_chars == 0:
            logger.warning(f"No data found for {ticker}, skipping.")
            return False

        input_hash = compute_input_hash(ctx, LLM_MODEL_SUMMARY, PROMPT_VERSION)

        if dry_run:
            logger.info(
                f"[dry-run] {ticker}: {ctx.doc_counts}, {ctx.total_chars:,} chars, "
                f"input_hash={input_hash[:12]}..."
            )
            return True

        # 1) Try cache
        cached = get_cached(ticker, input_hash)
        if cached is not None:
            logger.info(
                f"Cache HIT for {ticker} (hash={input_hash[:12]}..., "
                f"originally generated {cached.summary_date})"
            )
            header = f"# {ticker} Investment Analysis Report\n\n"
            header += f"> Generated on {cached.summary_date.isoformat()} (cached)\n"
            header += f"> Data: {ctx.doc_counts}\n\n"
            path = save_report(ticker, header + cached.content)
            logger.info(f"Report saved from cache: {path}")
            return True

        # 2) Miss — call the LLM
        logger.info(f"Cache MISS for {ticker} (hash={input_hash[:12]}...) — calling LLM")
        report = generate_summary(ctx)

        # 3) Write back to cache (best-effort, won't raise)
        put_cached(
            ticker=ticker,
            input_hash=input_hash,
            content=report,
            model=LLM_MODEL_SUMMARY,
            prompt_version=PROMPT_VERSION,
            source_doc_ids=ctx.source_doc_ids,
        )

        header = f"# {ticker} Investment Analysis Report\n\n"
        header += f"> Generated on {date.today().isoformat()}\n"
        header += f"> Data: {ctx.doc_counts}\n\n"

        path = save_report(ticker, header + report)
        logger.info(f"Report saved: {path}")
        return True

    except Exception as e:
        logger.error(f"Failed to process {ticker}: {e}", exc_info=True)
        return False


def _parse_skill_args(raw: str | None) -> dict:
    """Accept either JSON ('{"tickers":["AAPL","MSFT"]}') or a simple
    comma-separated k=v string ('ticker=AAPL,dry_run=true'). The k=v form
    is convenient for one-off CLI use; JSON is required for nested values
    like lists."""
    if not raw:
        return {}
    s = raw.strip()
    if s.startswith("{"):
        return json.loads(s)
    out: dict = {}
    for pair in s.split(","):
        if not pair.strip():
            continue
        if "=" not in pair:
            raise ValueError(f"--args expected k=v pairs, got {pair!r}")
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        # Tiny type coercion so booleans aren't passed as the string "true".
        if v.lower() in {"true", "false"}:
            out[k] = (v.lower() == "true")
        elif v.lstrip("-").isdigit():
            out[k] = int(v)
        else:
            out[k] = v
    return out


def run_skill(name: str, args: dict) -> bool:
    """Dispatch to the skills/ framework. Returns True on success."""
    from skills import get_skill  # local import: skills/ may live outside venv
    try:
        skill = get_skill(name)
    except KeyError as e:
        logger.error(str(e))
        return False
    try:
        result = skill.run(**args)
    except TypeError as e:
        logger.error("Skill %r received bad args %s: %s", name, args, e)
        return False
    except Exception as e:
        logger.error("Skill %r failed: %s", name, e, exc_info=True)
        return False

    # Print the human-readable content + any artifact paths. Keep this
    # minimal — the skill itself already logs detail.
    print(result.content)
    for path in result.artifacts:
        logger.info("Artifact: %s", path)
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate investment analysis reports via Claude")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker", type=str, help="Comma-separated ticker symbols (e.g. AAPL,MSFT)")
    group.add_argument("--all", action="store_true", help="Process all active tickers from Supabase")
    group.add_argument("--skill", type=str, help="Run a skill by name (see `python -m summary.run --list-skills`)")
    group.add_argument("--list-skills", action="store_true", help="List available skills and exit")
    parser.add_argument("--dry-run", action="store_true", help="Fetch data only, skip Claude API call")
    parser.add_argument("--args", type=str, default="", help='Skill arguments: JSON ({"k":"v"}) or k=v,k=v')
    args = parser.parse_args()

    # ── skills branch ──
    if args.list_skills:
        from skills import list_skills, all_skill_specs
        names = list_skills()
        if not names:
            print("No skills registered.")
        else:
            print("Available skills:")
            for spec in all_skill_specs():
                print(f"  {spec['name']:<24} {spec['description']}")
        sys.exit(0)

    if args.skill:
        skill_args = _parse_skill_args(args.args)
        # --dry-run is a common flag; surface it to the skill if it accepts one.
        if args.dry_run and "dry_run" not in skill_args:
            skill_args["dry_run"] = True
        ok = run_skill(args.skill, skill_args)
        sys.exit(0 if ok else 1)

    # ── original CLI behavior (unchanged) ──
    if args.all:
        rows = get_tracked_tickers(active_only=True)
        tickers = [r["ticker"] for r in rows]
        logger.info(f"Processing all {len(tickers)} active tickers: {tickers}")
    else:
        tickers = [t.strip().upper() for t in args.ticker.split(",")]

    results = {"success": [], "failed": [], "skipped": []}

    for ticker in tickers:
        ok = process_ticker(ticker, dry_run=args.dry_run)
        if ok:
            results["success"].append(ticker)
        else:
            results["failed"].append(ticker)

    logger.info("=" * 50)
    s, f = results["success"], results["failed"]
    logger.info(f"Done. Success: {s}, Failed: {f}")

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
