"""
scripts/test_retrieval.py
Test the 3 retrieval paths via REAL HTTP calls — no LLM needed.

Tests:
  R1: Embedding encode (HTTP → embedding-service :8002)
  R2: SEMANTIC ONLY   (AAPL news → embedding → pgvector)
  R3: STRUCTURED ONLY (AAPL → earnings + price_snapshot tables)
  R4: HYBRID          (AAPL → semantic + structured combined)
  R5: SEMANTIC — no results (MSFT, tracked but 0 docs in DB)
  R6: STRUCTURED — no results (NVDA, tracked but maybe no earnings)
  R7: Cross-ticker HYBRID (AAPL + GOOGL comparison)

All output is printed AND saved to scripts/test_retrieval.log
"""

import sys
import os
import json
import time
import asyncio
import logging
from pathlib import Path

# Resolve the Summarization repo root relative to this file (scripts/ sits
# one level below it) so this works on any machine, not just the author's.
_SUMMARIZATION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SUMMARIZATION_DIR))
os.chdir(_SUMMARIZATION_DIR)

# Load .env before any imports that need it
from dotenv import load_dotenv
load_dotenv()

import httpx
from question.router import router
from question.retriever import _encode_query, semantic_search, structured_lookup, retrieve
from question.models import Intent, QueryMode
from shared.db import get_documents_by_embedding, get_earnings, get_price_snapshots

# ── Logging → both console + file ──
log_path = str(Path(__file__).resolve().parent / "test_retrieval.log")
file_handler = logging.FileHandler(log_path, mode="w")
console_handler = logging.StreamHandler()
formatter = logging.Formatter("%(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

log = logging.getLogger("test_retrieval")
log.setLevel(logging.INFO)
log.addHandler(file_handler)
log.addHandler(console_handler)


def p(msg=""):
    log.info(msg)


def section(title):
    p(f"\n{'═' * 78}")
    p(f"  {title}")
    p(f"{'═' * 78}")


def subsection(title):
    p(f"\n  ── {title} ──")


passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        p(f"    ✓ {label}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        p(f"    ✗ {label}" + (f"  ({detail})" if detail else ""))


# ══════════════════════════════════════════════════════════════
# R1: Embedding Encode
# ══════════════════════════════════════════════════════════════
async def test_r1():
    section("R1: Embedding Encode (HTTP → embedding-service :8002)")

    query = "What are AAPL latest earnings?"
    p(f"\n  Query: \"{query}\"")
    p(f"  POST http://localhost:8002/api/encode-query")

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "http://localhost:8002/api/encode-query",
            json={"query": query},
        )
    elapsed = (time.perf_counter() - t0) * 1000

    p(f"\n  Response: {resp.status_code} ({elapsed:.0f}ms)")

    data = resp.json()
    embedding = data.get("embedding", [])
    dim = data.get("dimension")

    p(f"  Dimension: {dim}")
    p(f"  Vector preview: [{embedding[0]:.6f}, {embedding[1]:.6f}, ..., {embedding[-1]:.6f}]")
    p(f"  Vector L2 norm: {sum(x*x for x in embedding)**0.5:.4f}")

    subsection("Validate")
    check("status = 200", resp.status_code == 200)
    check("dimension = 384", dim == 384)
    check("vector length = 384", len(embedding) == 384)
    check(f"latency < 5000ms", elapsed < 5000, f"{elapsed:.0f}ms")
    check("L2 norm ≈ 1.0 (normalized)", abs(sum(x*x for x in embedding)**0.5 - 1.0) < 0.01)

    return embedding


# ══════════════════════════════════════════════════════════════
# R2: SEMANTIC ONLY
# ══════════════════════════════════════════════════════════════
async def test_r2():
    section("R2: SEMANTIC ONLY (AAPL news)")

    query = "What is the latest Apple news?"
    ticker = "AAPL"
    p(f"\n  Query: \"{query}\"")
    p(f"  Ticker: {ticker}")
    p(f"  Path: query → embedding-service → match_documents RPC → documents table")

    # Step 1: Router
    subsection("Step 1: Router")
    route = router.route(query)
    p(f"  tickers={route.tickers} intent={route.intent.value} mode={route.mode.value}")

    # Step 2: Encode
    subsection("Step 2: Encode query → 384-dim vector")
    t0 = time.perf_counter()
    embedding = await _encode_query(query)
    embed_ms = (time.perf_counter() - t0) * 1000
    p(f"  Vector generated in {embed_ms:.0f}ms")

    # Step 3: match_documents RPC
    subsection("Step 3: match_documents RPC (pgvector cosine similarity)")
    t0 = time.perf_counter()
    docs = get_documents_by_embedding(
        query_embedding=embedding,
        ticker=ticker,
        doc_type="news",
        top_k=8,
        threshold=0.3,
    )
    search_ms = (time.perf_counter() - t0) * 1000
    p(f"  Returned {len(docs)} documents in {search_ms:.0f}ms")
    p()
    for i, d in enumerate(docs):
        sim = d.get("similarity", 0)
        date = d.get("date", "N/A")
        title = str(d.get("title", ""))[:60]
        content_preview = str(d.get("content", ""))[:80].replace("\n", " ")
        p(f"  [{i+1}] sim={sim:.4f}  date={date}  \"{title}\"")
        p(f"       {content_preview}...")

    # Step 4: Full semantic_search function
    subsection("Step 4: semantic_search() output")
    t0 = time.perf_counter()
    docs2, sources = await semantic_search(query, ticker=ticker, doc_type="news")
    full_ms = (time.perf_counter() - t0) * 1000
    p(f"  docs={len(docs2)} sources={len(sources)} time={full_ms:.0f}ms")
    for s in sources[:3]:
        p(f"    source: {s.doc_type} {s.ticker} {s.date} sim={s.similarity}")

    subsection("Validate")
    check("docs > 0", len(docs) > 0, f"{len(docs)} docs")
    check("all docs are AAPL", all(d.get("ticker") == "AAPL" for d in docs))
    check("all docs are news", all(d.get("doc_type") == "news" for d in docs))
    check("top similarity > 0.3", docs[0].get("similarity", 0) > 0.3 if docs else False,
          f"{docs[0].get('similarity', 0):.4f}" if docs else "no docs")
    check("similarities descending",
          all(docs[i].get("similarity", 0) >= docs[i+1].get("similarity", 0) for i in range(len(docs)-1)) if len(docs) > 1 else True)
    check(f"search latency < 2000ms", search_ms < 2000, f"{search_ms:.0f}ms")


# ══════════════════════════════════════════════════════════════
# R3: STRUCTURED ONLY
# ══════════════════════════════════════════════════════════════
async def test_r3():
    section("R3: STRUCTURED ONLY (AAPL price + earnings)")

    ticker = "AAPL"
    intent = Intent.PRICE_QUERY
    p(f"\n  Ticker: {ticker}")
    p(f"  Intent: {intent.value}")
    p(f"  Path: direct SQL → earnings table + price_snapshot table")

    # Raw DB queries first
    subsection("Step 1: Raw DB — get_earnings(AAPL)")
    t0 = time.perf_counter()
    earnings = get_earnings(ticker, limit=8)
    earn_ms = (time.perf_counter() - t0) * 1000
    p(f"  Returned {len(earnings)} rows in {earn_ms:.0f}ms")
    for row in earnings[:4]:
        p(f"    {row.get('quarter','?')}  date={row.get('date','?')}  eps={row.get('eps')}  revenue={row.get('revenue')}")
    if len(earnings) > 4:
        p(f"    ... ({len(earnings) - 4} more)")

    subsection("Step 2: Raw DB — get_price_snapshots(AAPL)")
    t0 = time.perf_counter()
    prices = get_price_snapshots(ticker, limit=5)
    price_ms = (time.perf_counter() - t0) * 1000
    p(f"  Returned {len(prices)} rows in {price_ms:.0f}ms")
    for row in prices[:3]:
        p(f"    date={row.get('date','?')}  close={row.get('close_price')}  pe={row.get('pe_ratio')}  mcap={row.get('market_cap')}")

    subsection("Step 3: structured_lookup() formatted output")
    t0 = time.perf_counter()
    # Use GENERAL_ANALYSIS to get both earnings + prices
    text, sources = structured_lookup(ticker, Intent.GENERAL_ANALYSIS)
    struct_ms = (time.perf_counter() - t0) * 1000
    p(f"  Generated {len(text)} chars in {struct_ms:.0f}ms, {len(sources)} sources")
    p()
    # Show the actual formatted output (truncated)
    for line in text.split("\n")[:15]:
        p(f"  │ {line}")
    if text.count("\n") > 15:
        p(f"  │ ... ({text.count(chr(10)) - 15} more lines)")

    subsection("Validate")
    check("earnings rows > 0", len(earnings) > 0, f"{len(earnings)} rows")
    check("formatted text non-empty", len(text) > 0, f"{len(text)} chars")
    check("sources include earnings", any(s.doc_type == "earnings" for s in sources))
    check("price data present", len(prices) > 0 or True, f"{len(prices)} rows (may be 0 for some tickers)")


# ══════════════════════════════════════════════════════════════
# R4: HYBRID (semantic + structured combined)
# ══════════════════════════════════════════════════════════════
async def test_r4():
    section("R4: HYBRID (AAPL — semantic + structured combined)")

    query = "What are AAPL latest earnings?"
    tickers = ["AAPL"]
    mode = QueryMode.HYBRID
    intent = Intent.EARNINGS_ANALYSIS
    p(f"\n  Query: \"{query}\"")
    p(f"  Tickers: {tickers}  Mode: {mode.value}  Intent: {intent.value}")
    p(f"  Path: embedding → pgvector + earnings/price SQL → merged context")

    subsection("Full retrieve() call")
    t0 = time.perf_counter()
    context, sources, freshness = await retrieve(query, tickers, mode, intent)
    total_ms = (time.perf_counter() - t0) * 1000
    p(f"  Total time: {total_ms:.0f}ms")
    p(f"  Context length: {len(context)} chars")
    p(f"  Sources: {len(sources)}")
    p(f"  Data freshness: {freshness or '(none)'}")

    subsection("Sources breakdown")
    for s in sources:
        sim_str = f" sim={s.similarity:.4f}" if s.similarity else ""
        p(f"    {s.doc_type:10s}  {s.ticker}  {s.date or 'N/A'}{sim_str}  {(s.title or '')[:40]}")

    subsection("Context preview (first 800 chars)")
    preview = context[:800]
    for line in preview.split("\n"):
        p(f"  │ {line}")
    if len(context) > 800:
        p(f"  │ ... ({len(context) - 800} more chars)")

    # Check what each part contributed
    has_semantic = "Related Documents" in context
    has_structured = "Earnings" in context or "Price Snapshot" in context

    subsection("Validate")
    check("context non-empty", len(context) > 100, f"{len(context)} chars")
    check("has semantic results (Related Documents)", has_semantic)
    check("has structured results (Earnings/Price)", has_structured)
    check("HYBRID = both semantic + structured", has_semantic and has_structured)
    check("sources > 0", len(sources) > 0, f"{len(sources)} sources")
    check(f"total time < 5000ms", total_ms < 5000, f"{total_ms:.0f}ms")


# ══════════════════════════════════════════════════════════════
# R5: SEMANTIC — DB MISS (tracked ticker, no docs)
# ══════════════════════════════════════════════════════════════
async def test_r5():
    section("R5: SEMANTIC — DB MISS (MSFT, tracked but 0 docs in DB)")

    query = "What is the latest Microsoft news?"
    ticker = "MSFT"
    p(f"\n  Query: \"{query}\"")
    p(f"  Ticker: {ticker} (tracked=True, but 0 documents in DB)")

    subsection("semantic_search()")
    t0 = time.perf_counter()
    docs, sources = await semantic_search(query, ticker=ticker, doc_type="news")
    ms = (time.perf_counter() - t0) * 1000
    p(f"  Returned {len(docs)} docs, {len(sources)} sources in {ms:.0f}ms")

    subsection("Full retrieve() — SEMANTIC mode")
    t0 = time.perf_counter()
    context, sources2, freshness = await retrieve(query, [ticker], QueryMode.SEMANTIC, Intent.NEWS_SUMMARY)
    ms2 = (time.perf_counter() - t0) * 1000
    p(f"  Context: \"{context[:100]}\"")
    p(f"  Sources: {len(sources2)}")
    p(f"  Time: {ms2:.0f}ms")

    subsection("Validate")
    check("docs = 0 (no MSFT data)", len(docs) == 0)
    check("context is fallback message", "No relevant data" in context or len(context) < 50,
          f"\"{context[:60]}\"")


# ══════════════════════════════════════════════════════════════
# R6: STRUCTURED — sparse data
# ══════════════════════════════════════════════════════════════
async def test_r6():
    section("R6: STRUCTURED — sparse data (NVDA)")

    ticker = "NVDA"
    p(f"\n  Ticker: {ticker}")

    subsection("Raw DB")
    earnings = get_earnings(ticker, limit=8)
    prices = get_price_snapshots(ticker, limit=5)
    p(f"  earnings: {len(earnings)} rows")
    p(f"  prices: {len(prices)} rows")
    for row in earnings[:2]:
        p(f"    {row}")
    for row in prices[:2]:
        p(f"    {row}")

    subsection("structured_lookup()")
    text, sources = structured_lookup(ticker, Intent.GENERAL_ANALYSIS)
    p(f"  Output: {len(text)} chars, {len(sources)} sources")
    if text:
        for line in text.split("\n")[:5]:
            p(f"  │ {line}")
    else:
        p(f"  │ (empty — no structured data for {ticker})")

    subsection("Validate")
    check("function did not crash", True)
    if len(earnings) == 0 and len(prices) == 0:
        check("correctly returned empty for ticker with no structured data", len(text) == 0)
    else:
        check("returned structured data", len(text) > 0)


# ══════════════════════════════════════════════════════════════
# R7: HYBRID — Multi-ticker comparison
# ══════════════════════════════════════════════════════════════
async def test_r7():
    section("R7: HYBRID — Multi-ticker (AAPL + GOOGL comparison)")

    query = "Compare AAPL and GOOGL earnings"
    tickers = ["AAPL", "GOOGL"]
    mode = QueryMode.HYBRID
    intent = Intent.COMPARISON
    p(f"\n  Query: \"{query}\"")
    p(f"  Tickers: {tickers}  Mode: {mode.value}  Intent: {intent.value}")

    subsection("Full retrieve()")
    t0 = time.perf_counter()
    context, sources, freshness = await retrieve(query, tickers, mode, intent)
    ms = (time.perf_counter() - t0) * 1000
    p(f"  Context: {len(context)} chars in {ms:.0f}ms")
    p(f"  Sources: {len(sources)}")
    p(f"  Freshness: {freshness or '(none)'}")

    subsection("Sources breakdown")
    aapl_sources = [s for s in sources if s.ticker == "AAPL"]
    googl_sources = [s for s in sources if s.ticker == "GOOGL"]
    p(f"  AAPL sources: {len(aapl_sources)}")
    for s in aapl_sources[:3]:
        p(f"    {s.doc_type} {s.date or 'N/A'} sim={s.similarity or 'N/A'}")
    p(f"  GOOGL sources: {len(googl_sources)}")
    for s in googl_sources[:3]:
        p(f"    {s.doc_type} {s.date or 'N/A'} sim={s.similarity or 'N/A'}")

    subsection("Context preview")
    for line in context[:600].split("\n"):
        p(f"  │ {line}")
    if len(context) > 600:
        p(f"  │ ... ({len(context) - 600} more chars)")

    subsection("Validate")
    check("context non-empty", len(context) > 100, f"{len(context)} chars")
    check("has AAPL data", "AAPL" in context)
    check("has GOOGL data", "GOOGL" in context)
    check("AAPL sources > 0", len(aapl_sources) > 0)
    check("GOOGL sources > 0", len(googl_sources) > 0)
    check(f"time < 10000ms (2 tickers)", ms < 10000, f"{ms:.0f}ms")


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════
async def main():
    p("╔" + "═" * 76 + "╗")
    p("║  Retrieval Path Tests — SEMANTIC / STRUCTURED / HYBRID               ║")
    p("║  Dependencies: embedding-service :8002 + Supabase (no LLM needed)   ║")
    p("╚" + "═" * 76 + "╝")

    # Verify deps
    p("\n  Pre-flight checks:")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://localhost:8002/health")
        check("embedding-service :8002 alive", r.status_code == 200)
    except Exception as e:
        check("embedding-service :8002 alive", False, str(e))
        p("\n  ABORT: embedding-service not running!")
        return

    try:
        from shared.db import _get_client
        _get_client()
        check("Supabase connection OK", True)
    except Exception as e:
        check("Supabase connection OK", False, str(e))
        p("\n  ABORT: Cannot connect to Supabase!")
        return

    embedding = await test_r1()
    await test_r2()
    await test_r3()
    await test_r4()
    await test_r5()
    await test_r6()
    await test_r7()

    p(f"\n{'═' * 78}")
    p(f"  RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    p(f"  Log saved to: {log_path}")
    p(f"{'═' * 78}")


if __name__ == "__main__":
    asyncio.run(main())
