"""
pipeline/run.py
Main pipeline orchestration: fetch → clean → chunk → embed → store

Usage:
  # Full run for all tracked tickers
  python -m pipeline.run

  # Single ticker debug
  python -m pipeline.run --ticker AAPL

  # Print only, no DB writes (local testing)
  python -m pipeline.run --dry-run

  # Combine both
  python -m pipeline.run --ticker AAPL --dry-run

Architecture principles (Scale-friendly):
  - Each ticker is processed independently; one failure doesn't affect others
  - Sleep 1s after each ticker to respect Finnhub 60 req/min limit
  - Embeddings are batched (100 chunks per request, reducing round trips)
  - DB upsert is idempotent, safe to re-run
  - --dry-run mode: all write operations only log, no actual Supabase writes
  - Prefect @flow/@task decorators provide orchestration, retry, and observability
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Union

import requests
from prefect import flow, task

from pipeline.cleaner import (
    clean_news_article,
    clean_filing_text,
    clean_xbrl_value,
    format_timestamp,
)
from pipeline.chunker import chunk_news, chunk_filing, chunk_xbrl_facts
from pipeline.store import (
    make_document_id,
    upsert_documents,
    upsert_earnings,
    upsert_price_snapshot,
    get_tracked_tickers,
    update_ticker_timestamps,
)
from pipeline.config import EMBEDDING_SERVICE_URL, FILING_REFRESH_DAYS, EDGAR_FACTS_MAX_MB, EMBED_BATCH
from pipeline.clients.finnhub_client import FinnhubClient
from pipeline.clients.edgar_client import EdgarClient
from pipeline.clients.fmp_client import FMPClient

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

TICKER_SLEEP = 1.0       # pause between tickers (seconds), respecting Finnhub 60 req/min
REQUEST_TIMEOUT = 30     # HTTP request timeout (seconds) for embedding-service
NEWS_LIMIT = 50          # news items per fetch (avoid overly large single requests)

# ── Regulatory keyword classifier ────────────────────────────────────────────

import re

_REGULATORY_PATTERN = re.compile(
    r"""
    \bSEC\b | \bFDA\b | \bFTC\b | \bDOJ\b | \bEPA\b | \bOSHA\b | \bCFPB\b
    | \bFINRA\b | \bOCC\b | \bFHFA\b
    | \b(?:lawsuit|litigation|sued|suing)\b
    | \b(?:settlement|settled|consent\s+decree)\b
    | \b(?:fine|fined|penalty|penalt(?:y|ies)|sanction(?:s|ed)?)\b
    | \b(?:subpoena|indictment|indicted|investigation|investigated|probe|probed)\b
    | \b(?:compliance|regulatory|regulator|enforcement)\b
    | \b(?:class[\s-]?action|antitrust|whistleblow)\b
    | \b(?:recall|recalled|warning\s+letter|cease[\s-]?and[\s-]?desist)\b
    | \b(?:data\s+breach|privacy\s+violation|GDPR|CCPA)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_regulatory(text: str) -> bool:
    """Return True if text contains regulatory / compliance keywords."""
    return bool(_REGULATORY_PATTERN.search(text))


# ── Client instances (module-level singletons) ────────────────────────────────

finnhub = FinnhubClient()
edgar = EdgarClient()
fmp = FMPClient()


# ── Embedding helper (still uses HTTP — embedding-service remains a separate service) ──

def _embed_batch(texts: List[str]) -> List[Optional[List[float]]]:
    """Send a batch of texts to the embedding-service, return a list of vectors.

    Sends in batches (EMBED_BATCH chunks/request), returns vectors in the same order as input.
    Failed batches are filled with None placeholders (no zero vectors written;
    callers should filter out None entries).
    """
    all_vectors: List[Optional[List[float]]] = []
    url = f"{EMBEDDING_SERVICE_URL}/api/encode"

    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i: i + EMBED_BATCH]
        try:
            r = requests.post(url, json={"texts": batch}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            all_vectors.extend(r.json()["embeddings"])
        except Exception as e:
            logger.error(f"embed batch {i // EMBED_BATCH + 1} failed: {e}")
            # None placeholders: callers filter these out, avoiding wasted Supabase quota
            all_vectors.extend([None] * len(batch))

    return all_vectors


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _latest_xbrl_date(facts_us_gaap: dict) -> Optional[str]:
    """Find the latest 'end' date from the XBRL us-gaap dict.

    Used to tag XBRL document chunks with the accurate data date,
    rather than the pipeline run date.
    """
    latest = ""
    for metric_data in facts_us_gaap.values():
        for unit_list in metric_data.get("units", {}).values():
            for entry in unit_list:
                end = entry.get("end", "")
                if end > latest:
                    latest = end
    return latest or None


def _parse_utc(ts: str) -> datetime:
    """Convert Supabase timestamp string → timezone-aware UTC datetime.

    Supabase timestamps may lack timezone info (naive).
    We always attach UTC to avoid errors when subtracting from datetime.now(timezone.utc).
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmp_doc_type(period: str) -> str:
    """Convert FMP period string → doc_type, matching schema-defined valid values.

    FMP annual report period="FY" → "10-K"
    FMP quarterly report period="Q1/Q2/Q3/Q4" → "10-Q"
    """
    return "10-K" if period == "FY" else "10-Q"


def _snap_to_quarter_end(date_str: str) -> str:
    """Snap a date to the nearest calendar quarter end (Mar 31, Jun 30, Sep 30, Dec 31).

    FMP uses fiscal quarter-end dates (e.g. AAPL: Dec 27), while Finnhub uses
    calendar quarter-end dates (Dec 31). Normalizing both to calendar quarters
    ensures the upsert on (ticker, quarter) matches correctly.

    Examples: "2025-12-27" → "2025-12-31", "2025-09-27" → "2025-09-30"
    """
    from datetime import date as dt_date
    d = dt_date.fromisoformat(date_str)
    month = d.month
    year = d.year
    if month <= 3:
        return f"{year}-03-31"
    elif month <= 6:
        return f"{year}-06-30"
    elif month <= 9:
        return f"{year}-09-30"
    else:
        return f"{year}-12-31"


def _quarter_label(date_str: str) -> str:
    """Convert a calendar-quarter-end date to a readable quarter label.

    Used as the `quarter` upsert key in the earnings table. Must be consistent
    across all data sources (Finnhub, FMP) to avoid duplicate rows for the same
    fiscal period. Downstream consumers (question-service YoY calc) parse this
    via regex `Q([1-4])\\s+(\\d{4})`.

    Examples:
        "2025-12-31" → "Q4 2025"
        "2025-09-30" → "Q3 2025"
        "2024-03-31" → "Q1 2024"
    """
    from datetime import date as dt_date
    d = dt_date.fromisoformat(_snap_to_quarter_end(date_str))
    q = (d.month - 1) // 3 + 1
    return f"Q{q} {d.year}"

# ── Single Ticker Processing ────────────────────────────────────────────────────────────

@task(name="process_ticker", log_prints=True)
def process_ticker(
    ticker: str,
    ticker_type: str,
    last_news_fetch: Optional[str] = None,    # ISO date, incremental news start point
    last_filing_fetch: Optional[str] = None,  # ISO date, used for FMP/EDGAR skip logic
    dry_run: bool = False,
    initial: bool = False,                    # True = first-time deep fetch, ignore all skip logic
) -> None:
    """Full processing of one ticker: fetch → clean → chunk → embed → store.

    ticker_type: "stock" or "etf"
      - stock: news + earnings + EDGAR facts + price snapshot
      - etf:   news + price snapshot (no earnings/filings)
    initial=True: called when adding a new ticker for the first time,
                  forces full fetch, ignores timestamp-based skip logic
    """
    logger.info(f"▶ {ticker} ({ticker_type}){' [INITIAL]' if initial else ''}")
    doc_rows: List[dict] = []
    today = _today_iso()

    # Whether to re-fetch FMP/EDGAR (skip if already fetched within 30 days, respecting FMP 250 req/day limit)
    needs_filing_refresh = (
        initial
        or last_filing_fetch is None
        or (datetime.now(timezone.utc) - _parse_utc(last_filing_fetch)).days >= FILING_REFRESH_DAYS
    )

    # ── 2. News (fetched for all ticker types) ───────────────────────────────────────
    # Incremental fetch: if last fetch time exists, only get new content, saving Finnhub 60 req/min quota
    news_days_back = 7
    if not initial and last_news_fetch:
        delta = (datetime.now(timezone.utc) - _parse_utc(last_news_fetch)).days
        news_days_back = max(1, min(delta + 1, 365))

    try:
        news_data = finnhub.get_company_news(ticker, days_back=news_days_back) or []
    except Exception as e:
        logger.warning(f"{ticker} news fetch failed: {e}")
        news_data = []

    for article in news_data:
        try:
            cleaned = clean_news_article(article)
            chunks = chunk_news(cleaned["content"])
            if not chunks:
                continue
            vectors = _embed_batch(chunks)
            # Classify: if headline or content contains regulatory keywords → "regulatory"
            raw_text = (cleaned.get("title", "") + " " + cleaned["content"]).strip()
            dtype = "regulatory" if _is_regulatory(raw_text) else "news"

            for chunk, vec in zip(chunks, vectors):
                if vec is None:
                    continue  # embedding failed, skip
                doc_rows.append({
                    "id":       make_document_id(ticker, cleaned["date"], chunk),
                    "content":  chunk,
                    "embedding": vec,
                    "ticker":   ticker,
                    "date":     cleaned["date"],
                    "source":   cleaned["source"],
                    "doc_type": dtype,
                    "title":    cleaned.get("title", ""),
                    # Source link for citations: only news has a clean canonical URL.
                    # Filings/earnings doc rows leave this NULL; the UI falls back
                    # to "<title> (no link)" for those.
                    "url":      cleaned.get("url") or None,
                })
        except Exception as e:
            logger.warning(f"{ticker} news article error: {e}")
    news_chunks = sum(1 for r in doc_rows if r['doc_type'] == 'news')
    reg_chunks  = sum(1 for r in doc_rows if r['doc_type'] == 'regulatory')
    logger.info(f"  news → {len(news_data)} articles → {news_chunks} news chunks + {reg_chunks} regulatory chunks")

    # ── 3. Stock-specific data ────────────────────────────────────────────────────────
    if ticker_type == "stock":

        # 3a. Earnings summary → earnings table + documents table (for semantic search)
        try:
            earnings_data = finnhub.get_earnings_surprises(ticker) or []
        except Exception as e:
            logger.warning(f"{ticker} earnings fetch failed: {e}")
            earnings_data = []

        earnings_rows: List[dict] = []
        earnings_doc_texts: List[str] = []   # collect all texts first
        earnings_doc_meta:  List[dict] = []  # corresponding metadata
        for e in earnings_data:
            try:
                period_raw = e.get("period", "")
                if not period_raw:
                    continue
                # Normalize to "Q3 2025" label so upsert key (ticker, quarter) is
                # consistent across Finnhub & FMP, and downstream YoY regex can parse it.
                quarter_label = _quarter_label(period_raw)
                date_iso      = _snap_to_quarter_end(period_raw)
                eps     = e.get("actual")        or e.get("estimate")        or 0.0
                revenue = e.get("revenueActual") or e.get("revenueEstimate") or 0.0
                earnings_rows.append({
                    "ticker":     ticker,
                    "quarter":    quarter_label,
                    "date":       date_iso,
                    "eps":        float(eps or 0),
                    "revenue":    int(float(revenue or 0)),  # BIGINT column cannot store floats
                    "net_income": None,
                    "guidance":   None,
                })
                text = (
                    f"{ticker} Q earnings for {quarter_label}: "
                    f"EPS {eps}, revenue {clean_xbrl_value(revenue, 'USD')}."
                )
                earnings_doc_texts.append(text)
                earnings_doc_meta.append({"quarter": quarter_label, "date": date_iso, "text": text})
            except Exception as exc:
                logger.warning(f"{ticker} earnings row error: {exc}")

        # Batch vectorize all at once, instead of sending a separate request per quarter
        if earnings_doc_texts:
            earnings_vecs = _embed_batch(earnings_doc_texts)
            for meta, vec in zip(earnings_doc_meta, earnings_vecs):
                if vec is None:
                    continue  # embedding failed, skip this entry
                doc_rows.append({
                    "id":        make_document_id(ticker, meta["date"], meta["text"]),
                    "content":   meta["text"],
                    "embedding": vec,
                    "ticker":    ticker,
                    "date":      meta["date"],
                    "source":    "finnhub",
                    "doc_type":  "earnings",
                })
        upsert_earnings(earnings_rows, dry_run=dry_run)
        logger.info(f"  earnings → {len(earnings_rows)} quarters")

        # 3b. EDGAR XBRL facts (same as FMP, skip if already fetched within 30 days)
        if not needs_filing_refresh:
            logger.info(f"  edgar facts → skipped (last fetch: {last_filing_fetch})")
        else:
            cik = edgar.ticker_to_cik(ticker)
            if cik:
                try:
                    facts_data = edgar.get_company_facts(cik)
                except Exception as e:
                    logger.warning(f"{ticker} EDGAR facts fetch failed: {e}")
                    facts_data = None

                # Check response size (EDGAR facts can be very large)
                if facts_data:
                    facts_size_mb = len(json.dumps(facts_data)) / 1024 / 1024
                    if facts_size_mb > EDGAR_FACTS_MAX_MB:
                        logger.warning(f"EDGAR facts {facts_size_mb:.1f}MB > {EDGAR_FACTS_MAX_MB}MB limit, skipping")
                        facts_data = None

                if facts_data and "facts" in facts_data:
                    us_gaap = facts_data["facts"].get("us-gaap", {})
                    xbrl_chunks = chunk_xbrl_facts(ticker, us_gaap)
                    if xbrl_chunks:
                        vectors = _embed_batch(xbrl_chunks)
                        xbrl_date = _latest_xbrl_date(us_gaap) or today
                        for chunk, vec in zip(xbrl_chunks, vectors):
                            if vec is None:
                                continue
                            doc_rows.append({
                                "id":       make_document_id(ticker, xbrl_date, chunk),
                                "content":  chunk,
                                "embedding": vec,
                                "ticker":   ticker,
                                "date":     xbrl_date,
                                "source":   "edgar",
                                "doc_type": "10-K",  # schema: "news"|"10-Q"|"10-K"|"earnings"
                            })
                        logger.info(f"  edgar facts → {len(xbrl_chunks)} chunks")

        # 3c. FMP financial statements (skip if already fetched within 30 days, respecting FMP 250 req/day quota)
        if not needs_filing_refresh:
            logger.info(f"  fmp statements → skipped (last fetch: {last_filing_fetch})")
        else:
            fmp_earnings_rows: List[dict] = []  # collect revenue/netIncome to backfill earnings table
            fmp_methods = {
                "income-statement": fmp.get_income_statement,
                "balance-sheet": fmp.get_balance_sheet,
            }
            for stmt_type, method in fmp_methods.items():
                try:
                    # limit=5 → FMP free-tier cap; 5 quarters = current Q + same Q prior year,
                    # enabling YoY comparison. limit>=6 returns 402 Payment Required.
                    stmt = method(ticker, limit=5) or []
                except Exception as e:
                    logger.warning(f"{ticker} FMP {stmt_type} fetch failed: {e}")
                    stmt = []

                for period in stmt:
                    try:
                        date_str = period.get("date") or today
                        fmp_doc_type = _fmp_doc_type(period.get("period", ""))
                        lines = [f"{ticker} {stmt_type} for period {date_str}:"]
                        for k, v in period.items():
                            if k in ("date", "symbol", "reportedCurrency", "cik",
                                     "fillingDate", "acceptedDate", "calendarYear",
                                     "period", "link", "finalLink"):
                                continue
                            # FMP occasionally returns string-typed numbers, normalize them
                            try:
                                num = float(v) if not isinstance(v, (int, float)) else v
                            except (TypeError, ValueError):
                                continue
                            if num != 0:
                                lines.append(f"  {k}: {clean_xbrl_value(num, 'USD')}")
                        text = "\n".join(lines)
                        chunks = chunk_filing(text)
                        vectors = _embed_batch(chunks)
                        for chunk, vec in zip(chunks, vectors):
                            if vec is None:
                                continue
                            doc_rows.append({
                                "id":       make_document_id(ticker, date_str, chunk),
                                "content":  chunk,
                                "embedding": vec,
                                "ticker":   ticker,
                                "date":     date_str,
                                "source":   "fmp",
                                "doc_type": fmp_doc_type,  # "10-K" or "10-Q"
                            })

                        # ── Backfill earnings table from FMP income-statement ──
                        # FMP has revenue, netIncome, eps that Finnhub doesn't provide.
                        # Upsert on (ticker, quarter) will update existing rows.
                        # Snap FMP date to calendar quarter end to match Finnhub's quarter key.
                        if stmt_type == "income-statement":
                            rev_raw = period.get("revenue") or 0
                            ni_raw = period.get("netIncome") or 0
                            eps_raw = period.get("epsDiluted") or period.get("eps")
                            quarter_iso   = _snap_to_quarter_end(date_str)
                            quarter_label = _quarter_label(date_str)
                            fmp_earnings_rows.append({
                                "ticker":     ticker,
                                "quarter":    quarter_label,  # "Q4 2025" format (matches Finnhub)
                                "date":       quarter_iso,
                                "eps":        float(eps_raw) if eps_raw else None,
                                "revenue":    int(float(rev_raw)),  # BIGINT column
                                "net_income": int(float(ni_raw)),   # BIGINT column
                                "guidance":   None,
                            })

                    except Exception as exc:
                        logger.warning(f"{ticker} {stmt_type} period error: {exc}")

            # Write FMP earnings backfill (overwrites Finnhub's incomplete rows)
            if fmp_earnings_rows:
                upsert_earnings(fmp_earnings_rows, dry_run=dry_run)
                logger.info(f"  fmp earnings backfill → {len(fmp_earnings_rows)} quarters (revenue + net_income)")

            logger.info(f"  fmp statements → done")

    # ── 4. Price snapshot (all tickers) ───────────────────────────────────────────
    try:
        quote = fmp.get_quote(ticker)
    except Exception as e:
        logger.warning(f"{ticker} FMP quote failed: {e}")
        quote = None

    if quote:
        try:
            price_rows = [{
                "ticker":     ticker,
                "date":       today,
                "close_price": quote.get("price") or quote.get("previousClose"),
                "pe_ratio":   None,
                "market_cap": int(float(quote.get("marketCap") or 0)) or None,
            }]
            if ticker_type == "stock":
                # Get PE ratio directly from Finnhub basic financials
                try:
                    fin = finnhub.get_basic_financials(ticker)
                    if fin:
                        price_rows[0]["pe_ratio"] = fin.get("peNormalizedAnnual")
                except Exception as e:
                    logger.warning(f"{ticker} Finnhub financials failed: {e}")
            upsert_price_snapshot(price_rows, dry_run=dry_run)
        except Exception as e:
            logger.warning(f"{ticker} price snapshot error: {e}")

    # ── 5. Write to documents table ──────────────────────────────────────────────────
    upsert_documents(doc_rows, dry_run=dry_run)
    logger.info(f"✔ {ticker}: {len(doc_rows)} document chunks written")
    # ── 6. Update tracked_tickers timestamps (for Person B to display data freshness) ────────
    update_ticker_timestamps(
        ticker,
        news_fetch=today,
        filing_fetch=today if needs_filing_refresh else None,
        dry_run=dry_run,
    )

# ── Main entry point ─────────────────────────────────────────────────────────────────────

@flow(name="quant-data-pipeline", log_prints=True)
def main() -> None:
    parser = argparse.ArgumentParser(description="Run QuantAgent data pipeline")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Process a single ticker (uppercase, e.g. AAPL)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log all operations but skip actual Supabase writes")
    parser.add_argument("--initial", action="store_true",
                        help="First-time deep fetch: ignore skip logic, pull all history")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("🔥 DRY RUN mode — no data will be written to Supabase")

    if args.ticker:
        ticker = args.ticker.upper()
        # Determine type via Finnhub profile: ETFs return {} (no name field)
        try:
            profile = finnhub.get_company_profile(ticker) or {}
        except Exception:
            profile = {}
        ticker_type = "stock" if profile.get("name") else "etf"
        logger.info(f"  type detected: {ticker_type} (profile name: {profile.get('name', 'N/A')})")
        process_ticker(
            ticker, ticker_type,
            dry_run=args.dry_run,
            initial=args.initial,
        )
    else:
        tickers = get_tracked_tickers()
        logger.info(f"Processing {len(tickers)} tracked tickers")
        failed: List[str] = []
        for row in tickers:
            try:
                ticker_type = row.get("ticker_type")
                if not ticker_type:
                    # Fallback: determine type via Finnhub profile
                    try:
                        profile = finnhub.get_company_profile(row["ticker"]) or {}
                    except Exception:
                        profile = {}
                    ticker_type = "stock" if profile.get("name") else "etf"
                process_ticker(
                    row["ticker"],
                    ticker_type,
                    last_news_fetch=row.get("last_news_fetch"),
                    last_filing_fetch=row.get("last_filing_fetch"),
                    dry_run=args.dry_run,
                    initial=args.initial,
                )
            except Exception as e:
                logger.error(f"FAILED {row['ticker']}: {e}", exc_info=True)
                failed.append(row["ticker"])
            finally:
                time.sleep(TICKER_SLEEP)  # respect Finnhub 60 req/min

        if failed:
            logger.warning(f"{len(failed)}/{len(tickers)} tickers failed: {failed}")
        if len(failed) == len(tickers):
            raise SystemExit(1)  # all failed → CI shows red


if __name__ == "__main__":
    main()
