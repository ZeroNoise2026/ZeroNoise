"""Smoke tests for research.cache.compute_input_hash.

Run: python -m pytest libs/research/tests   (from the repo root)
"""
from research.cache import compute_input_hash
from research.context import TickerContext


def _ctx(ticker="AAPL", ids=None):
    return TickerContext(ticker=ticker, source_doc_ids=list(ids or []))


def test_all():
    # 1. Determinism: same input -> same hash, repeatable.
    ctx = _ctx(ids=["news:a", "news:b", "filing:c"])
    h1 = compute_input_hash(ctx, "claude-sonnet-5-5", "v1")
    h2 = compute_input_hash(ctx, "claude-sonnet-5-5", "v1")
    assert h1 == h2, "Hash is not deterministic"
    assert len(h1) == 64, f"Hash should be 64 hex chars, got {len(h1)}"
    print("determinism OK")

    # 2. Order-invariance: shuffled doc IDs produce the same hash.
    ctx_a = _ctx(ids=["news:a", "news:b", "filing:c"])
    ctx_b = _ctx(ids=["filing:c", "news:b", "news:a"])
    h_a = compute_input_hash(ctx_a, "claude-sonnet-5-5", "v1")
    h_b = compute_input_hash(ctx_b, "claude-sonnet-5-5", "v1")
    assert h_a == h_b, "Hash should be order-invariant (we sort internally)"
    print("order-invariance OK")

    # 3. Sensitivity: changing any of (ticker, ids, model, prompt_version)
    #    must change the hash.
    base = compute_input_hash(_ctx(ids=["news:a"]), "claude-sonnet-5-5", "v1")
    assert compute_input_hash(_ctx("MSFT", ids=["news:a"]), "claude-sonnet-5-5", "v1") != base, \
        "Different ticker must change hash"
    assert compute_input_hash(_ctx(ids=["news:a", "news:b"]), "claude-sonnet-5-5", "v1") != base, \
        "Different doc set must change hash"
    assert compute_input_hash(_ctx(ids=["news:a"]), "claude-opus-5-5", "v1") != base, \
        "Different model must change hash"
    assert compute_input_hash(_ctx(ids=["news:a"]), "claude-sonnet-5-5", "v2") != base, \
        "Different prompt_version must change hash"
    print("sensitivity OK")

    # 4. Empty ids: still produces a stable hash (don't crash on no-data ticker).
    h_empty1 = compute_input_hash(_ctx(ids=[]), "claude-sonnet-5-5", "v1")
    h_empty2 = compute_input_hash(_ctx(ids=[]), "claude-sonnet-5-5", "v1")
    assert h_empty1 == h_empty2
    assert h_empty1 != base
    print("empty-ids OK")

    # 5. Case insensitivity on ticker (we upper() inside compute_input_hash).
    h_lower = compute_input_hash(_ctx("aapl", ids=["news:a"]), "claude-sonnet-5-5", "v1")
    h_upper = compute_input_hash(_ctx("AAPL", ids=["news:a"]), "claude-sonnet-5-5", "v1")
    assert h_lower == h_upper, "Ticker case should not affect hash"
    print("ticker-case-insensitive OK")

    print("\nAll cache smoke tests passed.")


if __name__ == "__main__":
    test_all()
