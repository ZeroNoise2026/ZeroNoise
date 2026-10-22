"""
scripts/test_t4_router.py
T4: Test the QueryRouter in isolation — pure in-memory, no DB, no services.

Verbose mode: shows the full internal routing path for each query.
"""

import sys
import re
import time
from pathlib import Path

# Resolve the apps/api root relative to this file (scripts/ sits
# one level below it) so this works on any machine, not just the author's.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from question.router import router, COMPANY_ALIASES, INTENT_KEYWORDS, INTENT_PRIORITY, COMMON_WORDS, INTENT_TO_MODE
from question.models import Intent, QueryMode


def trace_route(query):
    """Re-run the routing logic step by step, capturing each decision."""
    trace = {"query": query}

    # Step 1: _extract_tickers trace
    tickers_from_regex = []
    regex_matches = re.findall(r"\b([A-Z]{1,5})\b", query)
    for m in regex_matches:
        if m in COMMON_WORDS:
            tickers_from_regex.append(f"{m} (filtered: COMMON_WORDS)")
        else:
            tickers_from_regex.append(f"{m} ✓")
    trace["regex_matches"] = regex_matches
    trace["regex_detail"] = tickers_from_regex

    tickers_from_alias = []
    query_lower = query.lower()
    for alias, ticker in COMPANY_ALIASES.items():
        if alias.lower() in query_lower:
            tickers_from_alias.append(f'"{alias}" → {ticker}')
    trace["alias_matches"] = tickers_from_alias

    # Step 2: _detect_intent trace
    intent_scores = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        matched = [kw for kw in keywords if kw.lower() in query_lower]
        score = len(matched)
        priority = INTENT_PRIORITY.get(intent, 0)
        intent_scores[intent] = {
            "matched_keywords": matched,
            "score": score,
            "priority": priority,
        }
    trace["intent_scores"] = intent_scores

    # Step 3: actual router result
    result = router.route(query)
    trace["result"] = result

    # Step 4: next step in the pipeline
    next_steps = []
    if result.mode in (QueryMode.SEMANTIC, QueryMode.HYBRID):
        next_steps.append("→ semantic_search() via embedding-service + match_documents RPC")
    if result.mode in (QueryMode.STRUCTURED, QueryMode.HYBRID):
        next_steps.append("→ structured_lookup() from earnings/price_snapshot tables")
    trace["next_steps"] = next_steps

    return trace


TESTS = [
    {
        "name": "T4a — Tier 1 ticker + intent",
        "query": "What are AAPL latest earnings?",
        "expect_tickers": ["AAPL"],
        "expect_intent": Intent.EARNINGS_ANALYSIS,
        "expect_mode": QueryMode.HYBRID,
    },
    {
        "name": "T4b — Tier 2 (untracked ticker)",
        "query": "How is PLTR doing?",
        "expect_tickers": ["PLTR"],
        "expect_intent": None,
        "expect_mode": None,
    },
    {
        "name": "T4c — No ticker (general question)",
        "query": "What is a P/E ratio?",
        "expect_tickers": [],
        "expect_intent": Intent.PRICE_QUERY,
        "expect_mode": QueryMode.STRUCTURED,
    },
    {
        "name": "T4d — Company alias 'Apple' → AAPL",
        "query": "Is Apple a buy right now?",
        "expect_tickers": ["AAPL"],
        "expect_intent": Intent.TRADE_SUGGESTION,
        "expect_mode": QueryMode.HYBRID,
    },
    {
        "name": "T4e — Multiple tickers",
        "query": "Compare AAPL and GOOGL earnings",
        "expect_tickers": ["AAPL", "GOOGL"],
        "expect_intent": Intent.COMPARISON,
        "expect_mode": QueryMode.HYBRID,
    },
    {
        "name": "T4f — L1 score=0 (no keywords match)",
        "query": "Tell me something interesting",
        "expect_tickers": [],
        "expect_intent": Intent.GENERAL_ANALYSIS,
        "expect_mode": QueryMode.HYBRID,
    },
    {
        "name": "T4g — Alias 'Nvidia' + news intent",
        "query": "What is the latest Nvidia news?",
        "expect_tickers": ["NVDA"],
        "expect_intent": Intent.NEWS_SUMMARY,
        "expect_mode": QueryMode.SEMANTIC,
    },
    {
        "name": "T4h — Price query with ticker",
        "query": "What is TSLA stock price today?",
        "expect_tickers": ["TSLA"],
        "expect_intent": Intent.PRICE_QUERY,
        "expect_mode": QueryMode.STRUCTURED,
    },
]

print("=" * 78)
print("  T4: Router Unit Tests — Verbose Path Trace")
print("=" * 78)

passed = 0
failed = 0

for t in TESTS:
    start = time.perf_counter()
    tr = trace_route(t["query"])
    elapsed = (time.perf_counter() - start) * 1000
    result = tr["result"]

    # Validate
    errors = []
    actual_tickers = sorted(result.tickers)
    expected_tickers = sorted(t["expect_tickers"])
    if actual_tickers != expected_tickers:
        errors.append(f"tickers: expected {expected_tickers}, got {actual_tickers}")
    if t["expect_intent"] is not None and result.intent != t["expect_intent"]:
        errors.append(f"intent: expected {t['expect_intent'].value}, got {result.intent.value}")
    if t["expect_mode"] is not None and result.mode != t["expect_mode"]:
        errors.append(f"mode: expected {t['expect_mode'].value}, got {result.mode.value}")

    status = "FAIL" if errors else "PASS"
    if errors:
        failed += 1
    else:
        passed += 1

    # Print verbose output
    print(f"\n┌─ [{status}] {t['name']}  ({elapsed:.1f}ms)")
    print(f"│  Query: \"{t['query']}\"")
    print(f"│")
    print(f"│  Step 1: Extract Tickers")
    print(f"│    Regex [A-Z]{{1,5}}: {tr['regex_matches']}")
    if tr["regex_detail"]:
        for rd in tr["regex_detail"]:
            print(f"│      {rd}")
    else:
        print(f"│      (no uppercase words found)")
    if tr["alias_matches"]:
        print(f"│    Alias matches:")
        for am in tr["alias_matches"]:
            print(f"│      {am}")
    else:
        print(f"│    Alias matches: (none)")
    print(f"│    → Final tickers: {actual_tickers}")
    print(f"│")
    print(f"│  Step 2: Detect Intent (keyword scoring)")
    for intent, info in tr["intent_scores"].items():
        if info["score"] > 0:
            kws = ", ".join(f'"{k}"' for k in info["matched_keywords"])
            winner = " ◀ WINNER" if intent == result.intent else ""
            print(f"│    {intent.value:20s}  score={info['score']}  priority={info['priority']}  matched=[{kws}]{winner}")
    all_zero = all(info["score"] == 0 for info in tr["intent_scores"].values())
    if all_zero:
        print(f"│    (all scores = 0 → fallback to GENERAL_ANALYSIS, L2 LLM TODO)")
    print(f"│    → Intent: {result.intent.value}")
    print(f"│")
    print(f"│  Step 3: Map Intent → QueryMode")
    print(f"│    {result.intent.value} → {result.mode.value}")
    print(f"│")
    print(f"│  Output: RouterResult(")
    print(f"│    tickers = {result.tickers}")
    print(f"│    intent  = {result.intent.value}")
    print(f"│    mode    = {result.mode.value}")
    print(f"│  )")
    print(f"│")
    print(f"│  Next in pipeline (orchestrator would do):")
    for ns in tr["next_steps"]:
        print(f"│    {ns}")
    if errors:
        print(f"│")
        print(f"│  ✗ ERRORS: {' | '.join(errors)}")
    print(f"└{'─' * 77}")

print(f"\n{'=' * 78}")
print(f"  Results: {passed}/{passed+failed} passed, {failed} failed")
print(f"{'=' * 78}")
