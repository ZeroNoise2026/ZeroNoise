"""Smoke tests: orchestrator emits a `sources` event on all 3 paths.

Run from Summarization/:
    python -m tests.test_sources

Strategy: monkeypatch router/retrieve/generator/structured-gen so we never
hit Supabase or the LLM. We only assert the orchestrator's outgoing event
sequence contains a `sources` event with the expected shape.
"""
from __future__ import annotations

import asyncio
import os

# Stub env early so imports don't fail
os.environ.setdefault("SUPABASE_URL", "http://stub")
os.environ.setdefault("SUPABASE_KEY", "stub")
os.environ.setdefault("ANTHROPIC_API_KEY", "stub")
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://stub:8002")

from question import orchestrator as orch  # noqa: E402
from question.models import (  # noqa: E402
    AskRequest, Intent, QueryMode, Recency, RouterResult, Source,
)


def _route(intent: Intent, tickers, **kw):
    return RouterResult(
        tickers=tickers,
        intent=intent,
        mode=kw.get("mode", QueryMode.HYBRID),
        recency=kw.get("recency", Recency.MEDIUM),
        quarter=kw.get("quarter"),
    )


async def _collect(req):
    items = []
    async for x in orch.handle_ask_stream(req):
        if x is not None:
            items.append(x)
    return items


def _sources_event(items):
    """Return the (event_type, payload) for the first 'sources' event."""
    for kind, payload in items:
        if kind == "sources":
            return payload
    return None


def test_fast_path_emits_price_table_sources():
    # router → PRICE_QUERY for AAPL
    orch.router.route = lambda *_a, **_k: _route(Intent.PRICE_QUERY, ["AAPL"])
    # short-circuit the price renderer so we don't need DB/live API
    async def fake_fast(tickers):
        return "## AAPL\n**Price:** $189.42\n"
    orch._fast_price_response = fake_fast

    items = asyncio.run(_collect(AskRequest(query="aapl price")))
    src = _sources_event(items)
    assert src is not None, f"no sources event in {items}"
    assert len(src) == 1
    assert src[0]["doc_type"] == "price_table"
    assert src[0]["ticker"] == "AAPL"
    assert src[0]["url"] is None
    assert src[0]["label"].startswith("Live quote")
    print("fast path ✓")


def test_path_a_earnings_emits_earnings_table_sources():
    orch.router.route = lambda *_a, **_k: _route(
        Intent.EARNINGS_ANALYSIS, ["MSFT"], quarter="Q4 2025",
    )
    async def fake_struct(query, tickers, target_quarter=None):
        return "## MSFT Q4 2025 ...\n"
    orch._structured_earnings_response = fake_struct

    items = asyncio.run(_collect(AskRequest(query="msft latest earnings")))
    src = _sources_event(items)
    assert src is not None
    assert len(src) == 1
    assert src[0]["doc_type"] == "earnings_table"
    assert src[0]["ticker"] == "MSFT"
    assert src[0]["id"] == "table:earnings:MSFT:Q4 2025"
    print("path A earnings ✓")


def test_path_a_comparison_emits_per_ticker_sources():
    orch.router.route = lambda *_a, **_k: _route(
        Intent.COMPARISON, ["AAPL", "MSFT"], quarter=None,
    )
    async def fake_cmp(query, tickers, target_quarter=None):
        return "## AAPL vs MSFT ...\n"
    orch._structured_comparison_response = fake_cmp

    items = asyncio.run(_collect(AskRequest(query="compare aapl and msft")))
    src = _sources_event(items)
    assert src is not None
    ids = sorted(s["id"] for s in src)
    assert ids == ["table:earnings:AAPL:latest", "table:earnings:MSFT:latest"]
    assert all(s["doc_type"] == "earnings_table" for s in src)
    print("path A comparison ✓")


def test_path_b_emits_doc_sources_after_llm_stream():
    # NEWS_SUMMARY routes to legacy retrieve+LLM path.
    orch.router.route = lambda *_a, **_k: _route(
        Intent.NEWS_SUMMARY, ["AAPL"], mode=QueryMode.SEMANTIC,
    )
    # Make is_ingested say AAPL is tier1 so retrieve() runs.
    # Stub at the orchestrator namespace where it was imported (avoids
    # touching shared.db which lazily creates a real Supabase client and
    # blows up under sandbox proxies).
    orch.is_ingested = lambda t: True

    fake_sources = [
        Source(
            id="doc:abc12",
            doc_type="news",
            ticker="AAPL",
            date="2026-05-08",
            title="Apple beats Q2",
            url="https://example.com/apple-q2",
            similarity=0.81,
        ),
        Source(
            id="doc:def34",
            doc_type="news",
            ticker="AAPL",
            date="2026-05-07",
            title="iPhone sales up 20%",
            url=None,           # missing url branch — UI shows greyed text
            similarity=0.74,
        ),
    ]

    async def fake_retrieve(**_kw):
        return ("AAPL context body", fake_sources, "")
    orch.retrieve = fake_retrieve

    # generate_answer_stream is a sync generator yielding (type, text).
    def fake_gen(q, ctx, freshness=""):
        yield ("token", "Apple had a strong quarter.\n")
    orch.generate_answer_stream = fake_gen

    items = asyncio.run(_collect(AskRequest(query="latest aapl news")))
    src = _sources_event(items)
    assert src is not None, f"no sources event in {items}"
    assert len(src) == 2
    assert src[0]["url"] == "https://example.com/apple-q2"
    assert src[1]["url"] is None
    # Sources event should land AFTER token events (so UI renders block under
    # the answer).
    types = [k for k, _ in items]
    last_token_idx = max(i for i, t in enumerate(types) if t == "token")
    sources_idx = types.index("sources")
    assert sources_idx > last_token_idx
    print("path B ✓ (sources after tokens, NULL url preserved)")


def test_dedupe_sources_keeps_first():
    s1 = Source(id="doc:x", doc_type="news", ticker="AAPL", title="v1")
    s2 = Source(id="doc:x", doc_type="news", ticker="AAPL", title="v2")  # dup id
    s3 = Source(id="doc:y", doc_type="news", ticker="AAPL", title="z")
    out = orch._dedupe_sources([s1, s2, s3])
    assert [s["id"] for s in out] == ["doc:x", "doc:y"]
    assert out[0]["title"] == "v1"          # first-seen wins
    print("dedup ✓")


def main():
    test_fast_path_emits_price_table_sources()
    test_path_a_earnings_emits_earnings_table_sources()
    test_path_a_comparison_emits_per_ticker_sources()
    test_path_b_emits_doc_sources_after_llm_stream()
    test_dedupe_sources_keeps_first()
    print("\nAll sources tests passed.")


if __name__ == "__main__":
    main()
