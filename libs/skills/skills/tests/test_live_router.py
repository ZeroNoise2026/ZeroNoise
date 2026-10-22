"""
skills/tests/test_live_router.py
Real Claude API smoke test — only the router, not the full skills.

We deliberately don't invoke generate_report / compare_tickers here because
those would hit Supabase (slower, and prone to env-specific data state).
Routing is the cheapest end-to-end check: ~1 small LLM call per query.

Requires ANTHROPIC_API_KEY in .env. Run from repo root:

    python -m skills.tests.test_live_router
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Pull ANTHROPIC_API_KEY from the central secrets file that ./dev.sh manages.
try:
    from dotenv import load_dotenv
except ImportError:
    print("python-dotenv not installed; assuming env is already loaded")
else:
    env_path = Path(os.getenv("QUANT_SECRETS_FILE", Path.home() / ".config/quantagent/secrets.env"))
    if env_path.exists():
        load_dotenv(env_path)


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("SKIP: ANTHROPIC_API_KEY not set")
        return 0

    from skills.router import route

    cases = [
        # (query, expected_skill_or_None, must_contain_args)
        ("Generate a report on AAPL",     "generate_report",  {"ticker": "AAPL"}),
        ("Compare AAPL vs MSFT please",   "compare_tickers",  {"tickers": ["AAPL", "MSFT"]}),
        ("What's the weather in Tokyo?",  None,               {}),
    ]

    failures = 0
    for query, expected_skill, must_have in cases:
        print(f"\n→ {query!r}")
        decision = route(query)
        print(f"  raw: {decision.raw[:200]}")
        print(f"  skill={decision.skill!r}  args={decision.args}")
        if decision.skill != expected_skill:
            print(f"  ✗ expected skill={expected_skill!r}, got {decision.skill!r}")
            failures += 1
            continue
        for k, v in must_have.items():
            got = decision.args.get(k)
            if isinstance(v, list):
                # Order-insensitive list compare, case-insensitive elements.
                ok = (isinstance(got, list)
                      and {str(x).upper() for x in got} == {str(x).upper() for x in v})
            else:
                ok = got == v
            if not ok:
                print(f"  ✗ expected args[{k}]={v!r}, got {got!r}")
                failures += 1
                break
        else:
            print("  ✓")

    print(f"\n{'='*40}\nFailures: {failures}\n{'='*40}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
