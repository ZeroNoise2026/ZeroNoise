"""
reports/cleanup.py
Delete summary_cache rows older than RETENTION_DAYS (default 90).

Usage:
    python -m reports.cleanup           # actually delete
    python -m reports.cleanup --dry-run # report only, no DB writes

Schedule: daily via .github/workflows/summary-cleanup.yml.
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import config  # noqa: F401  (loads .env)
from common import supabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reports.cleanup")

RETENTION_DAYS = 90


def _client():
    if not supabase.is_configured():
        raise SystemExit("SUPABASE_URL and SUPABASE_KEY must be set")
    return supabase.get_client()


def cleanup(dry_run: bool = False, retention_days: int = RETENTION_DAYS) -> int:
    """Delete rows older than retention_days. Returns number of rows deleted
    (or that would be deleted, if dry_run)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat()

    c = _client()

    # Count first so the log line has a real number even if delete returns
    # an empty payload.
    stale = (
        c.table("summary_cache")
        .select("ticker, input_hash, summary_date, created_at", count="exact")
        .lt("created_at", cutoff_iso)
        .execute()
    )
    count = stale.count or 0

    if count == 0:
        logger.info(f"Nothing to delete: 0 rows older than {cutoff_iso}")
        return 0

    if dry_run:
        # Log a few sample rows so the user can sanity-check
        sample = stale.data[:5] if stale.data else []
        logger.info(
            f"[dry-run] Would delete {count} rows older than {cutoff_iso} "
            f"(retention={retention_days}d). Sample: {sample}"
        )
        return count

    deleted = (
        c.table("summary_cache")
        .delete()
        .lt("created_at", cutoff_iso)
        .execute()
    )
    actual = len(deleted.data) if deleted.data else count
    logger.info(
        f"Deleted {actual} rows from summary_cache (cutoff={cutoff_iso}, "
        f"retention={retention_days}d)"
    )
    return actual


def main():
    parser = argparse.ArgumentParser(description="Prune summary_cache rows older than 90 days")
    parser.add_argument("--dry-run", action="store_true", help="Don't delete, just report")
    parser.add_argument(
        "--retention-days", type=int, default=RETENTION_DAYS,
        help=f"Override retention window (default {RETENTION_DAYS})",
    )
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run, retention_days=args.retention_days)


if __name__ == "__main__":
    main()
