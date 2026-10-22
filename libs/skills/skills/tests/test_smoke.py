"""
skills/tests/test_smoke.py
End-to-end pipeline smoke tests — no real API calls.

What we're proving:
  1. The framework imports cleanly (no circulars, no missing deps).
  2. Registry discovers generate_report and exposes its spec.
  3. Router parses a mocked LLM response into a RouteDecision.
  4. Skill.run() routes the call through registry → skill → SkillResult,
     with all external IO (Claude + Supabase) mocked.

Run from the repo root:

    python -m skills.tests.test_smoke
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest import mock


# ─────────────────────────────────────────────
# Test harness
# ─────────────────────────────────────────────
PASSED = 0
FAILED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {detail}")


# ─────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────
def test_registry_discovery() -> None:
    print("\n[1] registry discovery")
    # Force a fresh scan so prior imports don't mask failures.
    from skills import registry
    registry.reload()

    names = registry.list_skills()
    check("generate_report registered", "generate_report" in names,
          f"got {names}")

    specs = registry.all_skill_specs()
    spec = next((s for s in specs if s["name"] == "generate_report"), None)
    check("spec has description", bool(spec and spec.get("description")))
    check("spec has required ticker param",
          bool(spec and "ticker" in spec["parameters"].get("required", [])))


def test_router_no_api_key() -> None:
    print("\n[2] router with no API key returns no-route")
    import os
    from skills import router

    with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        # has_api_key reads env on each call, so this works without reload.
        decision = router.route("generate a report on AAPL")
    check("skill is None", decision.skill is None,
          f"got {decision.skill!r}")
    check("args is empty dict", decision.args == {})


def test_router_parses_mocked_response() -> None:
    print("\n[3] router parses a mocked LLM JSON response")
    from skills import router

    fake_response = json.dumps(
        {"skill": "generate_report", "args": {"ticker": "AAPL"}}
    )
    with mock.patch.object(router, "_call_router_llm", return_value=fake_response):
        decision = router.route("Generate a report on AAPL")

    check("skill routed", decision.skill == "generate_report",
          f"got {decision.skill!r}")
    check("ticker extracted",
          decision.args.get("ticker") == "AAPL",
          f"got {decision.args!r}")


def test_router_handles_code_fence() -> None:
    print("\n[4] router tolerates ```json fences")
    from skills import router

    fenced = "```json\n" + json.dumps({"skill": None}) + "\n```"
    with mock.patch.object(router, "_call_router_llm", return_value=fenced):
        decision = router.route("hello")
    check("parses through fence", decision.skill is None,
          f"got {decision.skill!r}")


def test_generate_report_dry_run_with_mocked_supabase(tmp_path: Path) -> None:
    print("\n[5] generate_report dry_run with mocked Supabase fetch")
    # Inject fakes for the Summarization-package imports the skill makes.
    # The skill imports lazily inside run(), so we patch sys.modules before
    # calling it.
    fake_ctx = types.SimpleNamespace(
        ticker="AAPL",
        total_chars=12345,
        doc_counts={"news": 10, "filings": 2},
        source_doc_ids=["doc-1", "doc-2"],
    )

    fake_config = types.ModuleType("config")
    fake_config.LLM_MODEL_SUMMARY = "claude-sonnet-5-5"
    fake_config.OUTPUT_DIR = tmp_path

    fake_fetcher = types.ModuleType("summary.fetcher")
    fake_fetcher.fetch_context = lambda ticker: fake_ctx

    fake_prompts = types.ModuleType("summary.prompts")
    fake_prompts.PROMPT_VERSION = "test-v1"

    fake_cache = types.ModuleType("summary.cache")
    fake_cache.compute_input_hash = lambda ctx, m, v: "deadbeef" * 8
    fake_cache.get_cached = lambda t, h: None
    fake_cache.put_cached = lambda **kw: None

    fake_summarizer = types.ModuleType("summary.summarizer")
    fake_summarizer.generate_summary = lambda ctx: "## Mocked report body"

    # Parent package shells so `from summary.X import Y` resolves
    fake_summary_pkg = types.ModuleType("summary")
    fake_summary_pkg.__path__ = []  # mark as package

    patched = {
        "config": fake_config,
        "summary": fake_summary_pkg,
        "summary.fetcher": fake_fetcher,
        "summary.prompts": fake_prompts,
        "summary.cache": fake_cache,
        "summary.summarizer": fake_summarizer,
    }
    with mock.patch.dict(sys.modules, patched):
        from skills import get_skill, registry
        registry.reload()
        result = get_skill("generate_report").run(ticker="aapl", dry_run=True)

    check("dry_run returns SkillResult", result is not None)
    check("content non-empty", bool(result.content))
    check("meta.ticker uppercased", result.meta.get("ticker") == "AAPL")
    check("meta.dry_run true", result.meta.get("dry_run") is True)
    check("no artifacts in dry_run", result.artifacts == [])


def test_compare_tickers_with_mocks() -> None:
    print("\n[7] compare_tickers with mocked Supabase + mocked LLM")
    # Two fake contexts — one populated, one empty (tests `missing` path).
    fake_aapl = types.SimpleNamespace(
        ticker="AAPL",
        total_chars=200,
        news_text="AAPL news body",
        regulatory_text="",
        filings_text="AAPL 10-Q excerpt",
        earnings_text="EPS 1.23 (beat)",
        price_text="$185.40, P/E 28",
        doc_counts={"news": 1, "filings": 1},
        source_doc_ids=[],
    )
    fake_msft = types.SimpleNamespace(
        ticker="MSFT",
        total_chars=180,
        news_text="MSFT news body",
        regulatory_text="",
        filings_text="MSFT 10-K snippet",
        earnings_text="EPS 2.45 (beat)",
        price_text="$420.10, P/E 35",
        doc_counts={"news": 1, "filings": 1},
        source_doc_ids=[],
    )
    fake_xxx = types.SimpleNamespace(  # no data — should be marked missing
        ticker="XXX",
        total_chars=0,
        news_text="", regulatory_text="", filings_text="",
        earnings_text="", price_text="",
        doc_counts={}, source_doc_ids=[],
    )
    ctx_by_ticker = {"AAPL": fake_aapl, "MSFT": fake_msft, "XXX": fake_xxx}

    fake_fetcher = types.ModuleType("summary.fetcher")
    fake_fetcher.fetch_context = lambda t: ctx_by_ticker[t]
    fake_summary_pkg = types.ModuleType("summary")
    fake_summary_pkg.__path__ = []

    chat_calls: list[dict] = []

    def fake_chat(messages, *, model, **kw):
        chat_calls.append({"model": model, "messages": messages})
        return ("# Mocked comparison\n| ticker | foo |\n", {"prompt_tokens": 100,
                                                             "completion_tokens": 50,
                                                             "total_tokens": 150})

    with mock.patch.dict(sys.modules, {
        "summary": fake_summary_pkg,
        "summary.fetcher": fake_fetcher,
    }):
        # Patch the LLM call inside the skill module.
        from skills.compare_tickers import skill as ct_skill_mod
        with mock.patch.object(ct_skill_mod, "chat", fake_chat):
            from skills import get_skill, registry
            registry.reload()
            result = get_skill("compare_tickers").run(
                tickers=["aapl", "msft", "xxx"],
                focus="valuation",
            )

    check("chat called once", len(chat_calls) == 1)
    check("uppercased tickers in meta",
          result.meta.get("tickers") == ["AAPL", "MSFT", "XXX"])
    check("XXX marked missing", result.meta.get("missing") == ["XXX"])
    check("content non-empty", bool(result.content))
    check("no artifacts (comparisons aren't persisted)", result.artifacts == [])
    if chat_calls:
        user_msg = chat_calls[0]["messages"][1]["content"]
        check("user prompt mentions AAPL", "AAPL" in user_msg)
        check("user prompt mentions MSFT", "MSFT" in user_msg)
        check("user prompt mentions focus 'valuation'", "valuation" in user_msg)
        check("user prompt notes missing XXX", "XXX" in user_msg)


def test_compare_tickers_rejects_singletons() -> None:
    print("\n[8] compare_tickers rejects <2 tickers")
    from skills import get_skill, registry
    registry.reload()
    raised = False
    try:
        get_skill("compare_tickers").run(tickers=["AAPL"])
    except ValueError:
        raised = True
    check("ValueError on 1 ticker", raised)


def test_cli_args_parser() -> None:
    print("\n[9] CLI _parse_skill_args handles JSON and k=v")
    # Loading summary.run pulls in supabase + the full DB stack, which we
    # don't want in the smoke suite. Instead, extract the function source
    # from the file and exec just that — the parser is self-contained.
    run_py = Path(__file__).resolve().parents[4] / "apps" / "summarization" / "summary" / "run.py"
    if not run_py.is_file():
        # The sibling Summarization repo isn't checked out next to this one.
        # Skip rather than crash: working from a standalone Skills clone is
        # legitimate, and this test only covers that repo's CLI arg parser.
        print(f"    SKIP: sibling repo not present at {run_py}")
        return
    src = run_py.read_text(encoding="utf-8")
    # Naive but sufficient: pull the function out by its definition line.
    marker = "def _parse_skill_args"
    if marker not in src:
        check("found _parse_skill_args in run.py", False)
        return
    start = src.index(marker)
    # Find the next top-level def/class as the function end.
    end_markers = ["\ndef ", "\nclass "]
    end = min(
        (src.index(m, start + 1) for m in end_markers if m in src[start + 1:]),
        default=len(src),
    )
    func_src = src[start:end]
    ns: dict = {"json": __import__("json")}
    exec(func_src, ns)
    fn = ns["_parse_skill_args"]

    check("empty input → {}", fn("") == {})
    check("JSON parses",
          fn('{"tickers":["AAPL","MSFT"],"focus":"valuation"}')
          == {"tickers": ["AAPL", "MSFT"], "focus": "valuation"})
    check("k=v parses",
          fn("ticker=AAPL,dry_run=true") == {"ticker": "AAPL", "dry_run": True})
    check("k=v coerces ints", fn("limit=10") == {"limit": 10})


def test_generate_report_full_path_with_mocks(tmp_path: Path) -> None:
    print("\n[6] generate_report full path writes file & returns artifact")
    fake_ctx = types.SimpleNamespace(
        ticker="MSFT",
        total_chars=5000,
        doc_counts={"news": 5},
        source_doc_ids=["doc-A"],
    )

    fake_config = types.ModuleType("config")
    fake_config.LLM_MODEL_SUMMARY = "claude-sonnet-5-5"
    fake_config.OUTPUT_DIR = tmp_path

    fake_fetcher = types.ModuleType("summary.fetcher")
    fake_fetcher.fetch_context = lambda ticker: fake_ctx

    fake_prompts = types.ModuleType("summary.prompts")
    fake_prompts.PROMPT_VERSION = "test-v1"

    fake_cache = types.ModuleType("summary.cache")
    fake_cache.compute_input_hash = lambda ctx, m, v: "feedface" * 8
    fake_cache.get_cached = lambda t, h: None
    put_calls: list[dict] = []
    fake_cache.put_cached = lambda **kw: put_calls.append(kw)

    fake_summarizer = types.ModuleType("summary.summarizer")
    fake_summarizer.generate_summary = lambda ctx: "## Real-looking report"

    fake_summary_pkg = types.ModuleType("summary")
    fake_summary_pkg.__path__ = []

    patched = {
        "config": fake_config,
        "summary": fake_summary_pkg,
        "summary.fetcher": fake_fetcher,
        "summary.prompts": fake_prompts,
        "summary.cache": fake_cache,
        "summary.summarizer": fake_summarizer,
    }
    with mock.patch.dict(sys.modules, patched):
        from skills import get_skill, registry
        registry.reload()
        result = get_skill("generate_report").run(ticker="MSFT")

    check("result has one artifact", len(result.artifacts) == 1,
          f"got {result.artifacts!r}")
    if result.artifacts:
        check("artifact file exists", result.artifacts[0].exists())
        check("artifact under tmp_path",
              str(result.artifacts[0]).startswith(str(tmp_path)))
        check("artifact contains report body",
              "Real-looking report" in result.artifacts[0].read_text())
    check("put_cached was called once", len(put_calls) == 1,
          f"got {len(put_calls)} calls")
    check("cache_hit false in meta", result.meta.get("cache_hit") is False)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main() -> int:
    tmp_root = Path("/tmp/skills_smoke_test")
    tmp_root.mkdir(exist_ok=True)

    test_registry_discovery()
    test_router_no_api_key()
    test_router_parses_mocked_response()
    test_router_handles_code_fence()
    # Create each temp dir BEFORE the test that writes into it. These mkdirs
    # used to run *after* their tests, so a fresh machine failed on test [6]
    # with FileNotFoundError and only passed on the second run, once the
    # trailing mkdir had left the directory behind.
    (tmp_root / "dry").mkdir(parents=True, exist_ok=True)
    test_generate_report_dry_run_with_mocked_supabase(tmp_root / "dry")
    (tmp_root / "full").mkdir(parents=True, exist_ok=True)
    test_generate_report_full_path_with_mocks(tmp_root / "full")
    test_compare_tickers_with_mocks()
    test_compare_tickers_rejects_singletons()
    test_cli_args_parser()

    print(f"\n{'='*40}")
    print(f"  PASSED: {PASSED}")
    print(f"  FAILED: {FAILED}")
    print(f"{'='*40}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
