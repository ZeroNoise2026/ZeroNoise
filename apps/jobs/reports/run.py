"""
reports/run.py
Report job: generate research reports for tickers, or run any skill.

Usage (from apps/jobs/):
    python -m reports.run --ticker AAPL
    python -m reports.run --ticker AAPL,MSFT,NVDA
    python -m reports.run --all
    python -m reports.run --ticker AAPL --dry-run
    python -m reports.run --skill compare_tickers --args '{"tickers":["AAPL","MSFT"]}'
    python -m reports.run --list-skills
"""

import argparse
import json
import logging
import sys

from config import OUTPUT_DIR
from research.data import get_tracked_tickers
from research.report import NoDataError, build_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run")


def process_ticker(ticker: str, dry_run: bool = False) -> bool:
    """Build one report (cached or fresh) and save it. Returns True on success."""
    try:
        report = build_report(ticker, dry_run=dry_run)
    except NoDataError as e:
        logger.warning(str(e))
        return False
    except Exception as e:
        logger.error(f"Failed to process {ticker}: {e}", exc_info=True)
        return False

    if dry_run:
        logger.info(
            f"[dry-run] {report.ticker}: {report.doc_counts}, {report.total_chars:,} chars, "
            f"input_hash={report.input_hash[:12]}..."
        )
        return True

    path = report.save(OUTPUT_DIR)
    logger.info(f"Report saved{' from cache' if report.cache_hit else ''}: {path}")
    return True


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
    from skills import get_skill
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
    group.add_argument("--skill", type=str, help="Run a skill by name (see `python -m reports.run --list-skills`)")
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

    # ── reports ──
    if args.all:
        rows = get_tracked_tickers(active_only=True)
        tickers = [r["ticker"] for r in rows]
        logger.info(f"Processing all {len(tickers)} active tickers: {tickers}")
    else:
        tickers = [t.strip().upper() for t in args.ticker.split(",")]

    failed = [t for t in tickers if not process_ticker(t, dry_run=args.dry_run)]
    logger.info("=" * 50)
    logger.info(f"Done. Success: {[t for t in tickers if t not in failed]}, Failed: {failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
