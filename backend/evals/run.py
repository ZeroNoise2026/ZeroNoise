"""
evals/run.py

CLI runner for the summarizer faithfulness suite.

    cd backend
    python -m evals.run --suite summarizer --n 3

Why n>1: current Claude models run at a fixed default temperature (custom
values are rejected), so generation is nondeterministic. A single run is an anecdote.
We run each fixture n times and threshold on the aggregate, while keeping every
raw per-run result in the report — averaging early destroys exactly the
variance you built the harness to see.

Ordering: deterministic checks run BEFORE the judge. They cost nothing, and by
default a structurally-invalid summary does not get judge tokens spent on it
(use --always-judge to override when diagnosing).

Exit code is 1 when any threshold is missed, so this drops straight into CI.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from evals import checks
from evals.judge import DEFAULT_JUDGE_MODEL, JudgeError, judge_summary

# Imported, never copied. The whole point is to evaluate the real code path.
import summarizer
from fetcher import TickerContext

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURES = EVALS_DIR / "fixtures" / "ticker_contexts.json"
DEFAULT_REPORT = EVALS_DIR / "report.json"

# Thresholds live in code, not in a wiki, so CI enforces the same numbers a
# human reads.
MIN_MEAN_FAITHFULNESS = 0.9
MAX_CONTRADICTIONS = 0
MIN_STRUCTURAL_PASS_RATE = 1.0
DEFAULT_MAX_ERROR_RATE = 0.2


# ─────────────────────────────────────────────────────────────
# Free observability: token counts + latency without touching summarizer.py
# ─────────────────────────────────────────────────────────────
# generate_summary() returns a bare string and throws its `usage` object away
# in a log line. We are not allowed to edit summarizer.py (and shouldn't want
# to — evals must not perturb the thing they measure), so instead we wrap the
# Claude client at runtime and record every call it makes. Nothing on disk
# changes; the patch lives and dies inside this process.

class _RecordingMessages:
    def __init__(self, inner: Any, sink: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._sink = sink

    def create(self, **kwargs: Any) -> Any:
        t0 = time.time()
        resp = self._inner.create(**kwargs)
        latency_ms = int((time.time() - t0) * 1000)
        u = resp.usage
        self._sink.append({
            "model": kwargs.get("model"),
            "prompt_tokens": u.input_tokens or 0,
            "completion_tokens": u.output_tokens or 0,
            "total_tokens": (u.input_tokens or 0) + (u.output_tokens or 0),
            "latency_ms": latency_ms,
        })
        return resp


class _RecordingClient:
    def __init__(self, inner: Any, sink: list[dict[str, Any]]) -> None:
        self._inner = inner
        self.messages = _RecordingMessages(inner.messages, sink)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


@contextmanager
def record_generator_calls() -> Iterator[list[dict[str, Any]]]:
    """Wrap summarizer's client so every completion it makes is recorded."""
    sink: list[dict[str, Any]] = []
    real = summarizer._get_client()
    original = summarizer._client
    summarizer._client = _RecordingClient(real, sink)
    try:
        yield sink
    finally:
        summarizer._client = original


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────
def load_fixtures(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"fixtures not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"fixtures must be a JSON array, got {type(data).__name__}")
    return data


def to_context(fixture: dict[str, Any]) -> TickerContext:
    """Build the real TickerContext the generator expects.

    Fixture field names line up 1:1 with the dataclass, so we reuse the real
    type instead of a look-alike. If the dataclass ever changes shape, this
    breaks loudly here rather than drifting quietly.
    """
    return TickerContext(
        ticker=fixture["ticker"],
        news_text=fixture.get("news_text") or "",
        filings_text=fixture.get("filings_text") or "",
        earnings_text=fixture.get("earnings_text") or "",
        price_text=fixture.get("price_text") or "",
    )


# ─────────────────────────────────────────────────────────────
# One run
# ─────────────────────────────────────────────────────────────
def execute_run(
    fixture: dict[str, Any],
    run_index: int,
    *,
    judge_model: str,
    judge_votes: int,
    use_judge: bool,
    always_judge: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "run": run_index,
        "status": "error",
        "summary": "",
        "generator": {},
        "structural": None,
        "judge": {"status": "skipped"},
        "error": None,
    }

    ctx = to_context(fixture)

    try:
        with record_generator_calls() as sink:
            t0 = time.time()
            summary = summarizer.generate_summary(ctx)
            wall_ms = int((time.time() - t0) * 1000)
    except Exception as e:
        record["error"] = f"generation failed: {type(e).__name__}: {e}"
        return record

    calls = list(sink)
    record["summary"] = summary
    record["generator"] = {
        "wall_ms": wall_ms,
        "model": calls[-1]["model"] if calls else None,
        "api_calls": len(calls),
        "prompt_tokens": sum(c["prompt_tokens"] for c in calls),
        "completion_tokens": sum(c["completion_tokens"] for c in calls),
        "total_tokens": sum(c["total_tokens"] for c in calls),
    }

    # ── deterministic checks first ──
    structural = checks.run_checks(summary, fixture)
    record["structural"] = structural

    if not use_judge:
        record["status"] = "pass" if structural["passed"] else "fail"
        return record

    if not structural["passed"] and not always_judge:
        # Cheap check already failed: don't pay the judge to confirm it.
        record["judge"] = {"status": "skipped", "reason": "structural checks failed"}
        record["status"] = "fail"
        return record

    try:
        jr = judge_summary(summary, fixture, model=judge_model, votes=judge_votes)
    except JudgeError as e:
        # A broken judge is neither a pass nor a fail. It is its own column.
        record["judge"] = {"status": "error", "error": str(e)}
        record["error"] = f"judge error: {e}"
        record["status"] = "error"
        return record

    payload = jr.to_dict()
    payload["status"] = "ok"
    record["judge"] = payload
    record["status"] = (
        "pass"
        if structural["passed"]
        and not jr.has_contradiction
        and jr.faithfulness >= MIN_MEAN_FAITHFULNESS
        else "fail"
    )
    return record


# ─────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────
def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [r for r in runs if r["judge"].get("status") == "ok"]
    errors = [r for r in runs if r["status"] == "error"]
    structural_done = [r for r in runs if r["structural"] is not None]

    scores = [r["judge"]["faithfulness"] for r in judged]
    contradictions = sum(r["judge"]["counts"]["contradicted"] for r in judged)
    struct_pass = sum(1 for r in structural_done if r["structural"]["passed"])
    latencies = [r["generator"]["wall_ms"] for r in runs if r.get("generator")]

    return {
        "runs": len(runs),
        "runs_passed": sum(1 for r in runs if r["status"] == "pass"),
        "runs_failed": sum(1 for r in runs if r["status"] == "fail"),
        "runs_errored": len(errors),
        "faithfulness_scores": scores,
        "mean_faithfulness": (sum(scores) / len(scores)) if scores else None,
        "contradictions": contradictions,
        "structural_checked": len(structural_done),
        "structural_passed": struct_pass,
        "structural_pass_rate": (struct_pass / len(structural_done)) if structural_done else None,
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else None,
        "latency_max_ms": max(latencies) if latencies else None,
        "total_tokens": sum(
            (r.get("generator") or {}).get("total_tokens", 0)
            + (r["judge"].get("usage", {}) or {}).get("total_tokens", 0)
            for r in runs
        ),
    }


def evaluate_thresholds(
    fixtures_out: list[dict[str, Any]], max_error_rate: float
) -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []

    total_runs = sum(f["aggregate"]["runs"] for f in fixtures_out)
    total_errors = sum(f["aggregate"]["runs_errored"] for f in fixtures_out)
    error_rate = (total_errors / total_runs) if total_runs else 0.0

    for f in fixtures_out:
        agg = f["aggregate"]
        mean = agg["mean_faithfulness"]
        results.append({
            "name": f"faithfulness[{f['fixture_id']}] >= {MIN_MEAN_FAITHFULNESS}",
            "value": None if mean is None else round(mean, 4),
            "met": mean is not None and mean >= MIN_MEAN_FAITHFULNESS,
            "note": "no judged runs" if mean is None else "",
        })

    total_contradictions = sum(f["aggregate"]["contradictions"] for f in fixtures_out)
    results.append({
        "name": f"contradictions <= {MAX_CONTRADICTIONS}",
        "value": total_contradictions,
        "met": total_contradictions <= MAX_CONTRADICTIONS,
        "note": "",
    })

    checked = sum(f["aggregate"]["structural_checked"] for f in fixtures_out)
    passed = sum(f["aggregate"]["structural_passed"] for f in fixtures_out)
    rate = (passed / checked) if checked else 0.0
    results.append({
        "name": f"structural pass rate >= {MIN_STRUCTURAL_PASS_RATE:.0%}",
        "value": round(rate, 4),
        "met": rate >= MIN_STRUCTURAL_PASS_RATE,
        "note": f"{passed}/{checked}",
    })

    # Without this, "9 judge errors + 1 perfect run" would report green.
    results.append({
        "name": f"judge error rate <= {max_error_rate:.0%}",
        "value": round(error_rate, 4),
        "met": error_rate <= max_error_rate,
        "note": f"{total_errors}/{total_runs}",
    })

    return all(r["met"] for r in results), results


# ─────────────────────────────────────────────────────────────
# Console output
# ─────────────────────────────────────────────────────────────
def _fmt_scores(scores: list[float]) -> str:
    return ", ".join(f"{s:.2f}" for s in scores) if scores else "-"


def print_table(fixtures_out: list[dict[str, Any]]) -> None:
    headers = ["FIXTURE", "TICKER", "PASS", "FAITHFULNESS/RUN", "STRUCT", "CONTRA", "ERR", "LAT p50/max", "TOKENS"]
    rows: list[list[str]] = []
    for f in fixtures_out:
        a = f["aggregate"]
        rows.append([
            f["fixture_id"],
            f["ticker"],
            f"{a['runs_passed']}/{a['runs']}",
            _fmt_scores(a["faithfulness_scores"]),
            f"{a['structural_passed']}/{a['structural_checked']}",
            str(a["contradictions"]),
            str(a["runs_errored"]),
            f"{a['latency_p50_ms']}/{a['latency_max_ms']} ms" if a["latency_p50_ms"] is not None else "-",
            f"{a['total_tokens']:,}",
        ])

    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print("\n" + line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def print_violations(fixtures_out: list[dict[str, Any]]) -> None:
    """Detail block — this is where the eval actually earns its keep."""
    shown = False
    for f in fixtures_out:
        for run in f["runs"]:
            struct = run.get("structural") or {}
            viols = struct.get("violations") or []
            contra = [
                c for c in (run["judge"].get("claims") or [])
                if c.get("verdict") == "contradicted"
            ]
            if not viols and not contra and run["status"] != "error":
                continue
            if not shown:
                print("\nFINDINGS")
                print("=" * 78)
                shown = True
            print(f"\n  {f['fixture_id']} run {run['run']} [{run['status']}]")
            for v in viols:
                print(f"    - structural: {v}")
            for c in contra:
                print(f"    - CONTRADICTED: {c['claim']}")
                print(f"        evidence [{c.get('evidence_source', '?')}]: {c.get('evidence', '')[:160]}")
            if run.get("error"):
                print(f"    - error: {run['error']}")
    if not shown:
        print("\nFINDINGS: none — every run passed every check.")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="QuantAgent eval suites. Run from the backend/ directory.",
    )
    p.add_argument("--suite", default="summarizer", choices=["summarizer"],
                   help="which suite to run (Tier B e2e is not implemented here)")
    p.add_argument("--n", type=int, default=3,
                   help="runs per fixture; generation is nondeterministic so >1 is required")
    p.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    p.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL,
                   help="must differ from the generator model (env: EVAL_JUDGE_MODEL)")
    p.add_argument("--judge-votes", type=int, default=1,
                   help="re-run verification N times and take a per-claim majority")
    p.add_argument("--max-error-rate", type=float, default=DEFAULT_MAX_ERROR_RATE)
    p.add_argument("--fixture-id", action="append", default=None,
                   help="only run these fixture_ids (repeatable)")
    p.add_argument("--no-judge", action="store_true",
                   help="structural checks only; free and instant")
    p.add_argument("--always-judge", action="store_true",
                   help="judge even when structural checks already failed")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.n < 1:
        raise SystemExit("--n must be >= 1")

    fixtures = load_fixtures(args.fixtures)
    if args.fixture_id:
        wanted = set(args.fixture_id)
        fixtures = [f for f in fixtures if f.get("fixture_id") in wanted]
        if not fixtures:
            raise SystemExit(f"no fixtures matched {sorted(wanted)}")

    use_judge = not args.no_judge
    print(f"suite={args.suite}  fixtures={len(fixtures)}  n={args.n}  "
          f"judge={'off' if args.no_judge else args.judge_model}"
          f"{f' votes={args.judge_votes}' if args.judge_votes > 1 else ''}")

    fixtures_out: list[dict[str, Any]] = []
    generator_models: set[str] = set()

    for fx in fixtures:
        fid = fx.get("fixture_id", "?")
        runs: list[dict[str, Any]] = []
        for i in range(1, args.n + 1):
            print(f"  {fid} run {i}/{args.n} ...", flush=True)
            rec = execute_run(
                fx, i,
                judge_model=args.judge_model,
                judge_votes=args.judge_votes,
                use_judge=use_judge,
                always_judge=args.always_judge,
            )
            m = (rec.get("generator") or {}).get("model")
            if m:
                generator_models.add(m)
            runs.append(rec)

        fixtures_out.append({
            "fixture_id": fid,
            "ticker": fx.get("ticker", "?"),
            "runs": runs,
            "aggregate": aggregate(runs),
        })

    ok, threshold_results = evaluate_thresholds(fixtures_out, args.max_error_rate)

    print_table(fixtures_out)
    print_violations(fixtures_out)

    print("\nTHRESHOLDS")
    print("=" * 78)
    for t in threshold_results:
        mark = "PASS" if t["met"] else "FAIL"
        note = f"  ({t['note']})" if t["note"] else ""
        print(f"  [{mark}] {t['name']}: {t['value']}{note}")

    # Self-grading bias is a real limitation, not a footnote. Say it out loud
    # whenever the judge shares a family with the generator.
    if use_judge and generator_models:
        if args.judge_model in generator_models:
            print(f"\n  WARNING: judge model ({args.judge_model}) is the SAME as a generator "
                  f"model. Scores are self-graded and biased upward.")
        else:
            print(f"\n  note: generator={sorted(generator_models)} judge={args.judge_model} "
                  f"(same provider — some self-preference bias remains)")

    report = {
        "suite": args.suite,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "n": args.n,
            "judge_model": None if args.no_judge else args.judge_model,
            "judge_votes": args.judge_votes,
            "fixtures_path": str(args.fixtures),
            "thresholds": {
                "min_mean_faithfulness": MIN_MEAN_FAITHFULNESS,
                "max_contradictions": MAX_CONTRADICTIONS,
                "min_structural_pass_rate": MIN_STRUCTURAL_PASS_RATE,
                "max_error_rate": args.max_error_rate,
            },
        },
        "generator_models_seen": sorted(generator_models),
        "fixtures": fixtures_out,
        "thresholds": threshold_results,
        "verdict": "pass" if ok else "fail",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport -> {args.out}")

    print(f"\nVERDICT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
