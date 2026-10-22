"""
briefing.py
Timezone-aware daily briefing scheduler.

Called hourly via GitHub Actions. For each user with briefing_enabled=True:
  - Check if their local time is in the 08:00-08:14 window
  - Check if today's briefing already exists
  - If both pass, generate and upsert the briefing

Usage:
    python briefing.py
"""

import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import BRIEFING_WINDOW_MINUTES
from db import get_users_needing_briefing, briefing_exists, upsert_briefing
from fetcher import fetch_context
from summarizer import generate_briefing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("briefing")


def is_in_briefing_window(tz_name: str, target_hour: int = 8) -> tuple[bool, str]:
    try:
        tz = ZoneInfo(tz_name)
    except KeyError:
        logger.warning(f"Unknown timezone '{tz_name}', falling back to America/New_York")
        tz = ZoneInfo("America/New_York")

    now_local = datetime.now(timezone.utc).astimezone(tz)
    local_date = now_local.strftime("%Y-%m-%d")
    in_window = now_local.hour == target_hour and now_local.minute < BRIEFING_WINDOW_MINUTES
    return in_window, local_date


def process_user(user: dict) -> bool:
    user_id = user["user_id"]
    tz_name = user["timezone"]
    tickers = user["tickers"]

    in_window, local_date = is_in_briefing_window(tz_name)
    if not in_window:
        return False

    if briefing_exists(user_id, local_date):
        logger.info(f"  {user_id}: briefing for {local_date} already exists, skipping")
        return False

    logger.info(f"  {user_id}: generating briefing for {local_date} ({tickers})")

    contexts = []
    for ticker in tickers:
        try:
            ctx = fetch_context(ticker, news_limit=50)
            if ctx.total_chars > 0:
                contexts.append(ctx)
        except Exception as e:
            logger.error(f"  Failed to fetch {ticker}: {e}")

    if not contexts:
        logger.warning(f"  {user_id}: no data for any ticker, skipping")
        return False

    try:
        content = generate_briefing(contexts)
        header = f"# Daily Briefing — {local_date}\n\n> Tickers: {', '.join(tickers)}\n\n"
        upsert_briefing(user_id, local_date, header + content, tickers)
        logger.info(f"  {user_id}: briefing saved for {local_date}")
        return True
    except Exception as e:
        logger.error(f"  {user_id}: failed to generate briefing: {e}")
        return False


def main():
    logger.info("Starting daily briefing scheduler...")
    users = get_users_needing_briefing()
    logger.info(f"Found {len(users)} users with briefing enabled + watchlist")

    if not users:
        logger.info("No users to process, exiting.")
        return

    results = {"generated": 0, "skipped": 0, "failed": 0}
    for user in users:
        try:
            ok = process_user(user)
            if ok:
                results["generated"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            logger.error(f"Error processing user {user['user_id']}: {e}")
            results["failed"] += 1

    logger.info(f"Done. {results}")
    if results["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
