"""Smoke test for COMMON_WORDS fix."""
from question.router import QueryRouter

r = QueryRouter()

tests = [
    ("What is the stock price for F", ["F"], "single-letter ticker F"),
    ("Tell me about AAPL", ["AAPL"], "normal ticker"),
    ("Compare ALL and NOW", ["ALL", "NOW"], "ALL/NOW no longer blocked"),
    ("What is new in the market", [], "new=common word, not ticker"),
    ("How has Tesla been doing", ["TSLA"], "alias extraction"),
    ("$F stock price", ["F"], "dollar-prefixed F"),
    ("IPO market trends", ["IPO"], "IPO no longer blocked"),
    ("I want to buy stocks", [], "no false positives"),
]

passed = 0
for query, expected, desc in tests:
    result = r.route(query)
    got = sorted(result.tickers)
    exp = sorted(expected)
    ok = got == exp
    if ok:
        passed += 1
        print(f"  OK  {desc}: {got}")
    else:
        print(f"  FAIL {desc}: expected {exp}, got {got}")

print(f"\n{passed}/{len(tests)} passed")
