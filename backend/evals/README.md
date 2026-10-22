# evals/ — summarizer faithfulness suite

## Run

```bash
cd backend                       # summarizer/config are top-level modules here
export ANTHROPIC_API_KEY=...     # or put it in backend/.env
python -m evals.run --suite summarizer --n 3
```

Useful flags: `--no-judge` (structural only, free and instant) · `--fixture-id FX2_missing_earnings` ·
`--judge-model claude-opus-5-5` · `--judge-votes 3` · `--always-judge` · `--out path.json`

## What is measured

Each of the 3 fixtures is generated `n` times (generation is `temperature=1.0`, so one run is an anecdote), then:

1. **Deterministic checks first** (`checks.py`, zero cost) — ≤300 words, a `##`/`###` header present, and no
   financial-statement figures when the fixture supplied no `earnings_text`.
2. **Claim-level judge** (`judge.py`) — extract atomic claims, then verify each against the context:
   `supported` / `unsupported` / `contradicted`, each with a verbatim evidence span.
   `faithfulness = supported / total_claims`.

Latency and prompt/completion token counts are recorded for **every** call, generator and judge.

## Thresholds (in code, enforced by exit code)

| Threshold | Value |
|---|---|
| Mean faithfulness, per fixture | ≥ 0.90 |
| Contradicted claims | 0 (any contradiction fails that fixture immediately) |
| Structural checks | 100% |
| Judge error rate | ≤ 20% |

`sys.exit(1)` if any threshold is missed. Report artifact: `evals/report.json`.

---

## Design notes

**Why claim-level, not a 1–10 score.** A holistic score is unstable across runs, can't tell you *which*
sentence was wrong, and collapses the two failures that need different fixes. `unsupported` means the
context was silent — a retrieval gap. `contradicted` means the model overrode evidence it was given — a
generation bug. Those get different owners, so the judge keeps them apart.

**Why the judge is told it has no outside knowledge.** A claim that is true in reality but absent from the
context is `unsupported`. That's what makes this a *faithfulness* measure rather than a correctness one.

**The judge is nondeterministic too.** `--judge-votes N` re-runs verification and takes a per-claim
majority. Ties resolve to the *less* severe verdict — one flaky vote shouldn't fail a build, since a single
`contradicted` sinks a whole fixture.

**Self-grading bias.** Generator and judge are both Claude. The runner defaults the judge to a different,
stronger model than the generator (`claude-sonnet-5-5` vs. the `claude-haiku-5-5` that `LLM_MODEL` selects)
and prints a warning if they ever coincide — but same-provider self-preference bias remains.
The real fix is a second provider's key; treat the absolute numbers as soft and the run-over-run *deltas*
as the signal.

**Why the fabrication regex is not `\$[\d.]+B`.** FX2's `price_text` contains `$94.20` and `-1.3%`. A naive
currency/percentage pattern flags a *correct* summary of that fixture as fabrication. So the check requires a
financial-statement term (EPS, revenue, gross margin…) *bound to* a figure by direction and proximity, and
explicitly excludes price/market-cap/P-E phrasing. Violations are reported with the offending snippet so
false positives are auditable rather than mysterious.

**Token counts without touching `summarizer.py`.** `generate_summary()` returns a bare string and drops its
`usage` object into a log line. Rather than edit it — evals shouldn't perturb what they measure — the runner
wraps the Claude client at runtime and records every completion. Nothing on disk changes.

**FX3 — faithfulness ≠ correctness.** FX3's context is *internally inconsistent*: `news_text` and
`earnings_text` state different revenue figures for the same quarter. A summary echoing the wrong one is
**perfectly faithful to the context** and this suite will score it 1.0. That is not a bug in the judge; it is
the ceiling of what any faithfulness metric can see. Fixing it is a *retrieval/ingest* problem, not a judging
one, and the design is source priority:

- **Structured beats unstructured** — an `earnings` table row outranks a number parsed out of a news blob.
- **Provenance tagged at ingest** — every chunk carries source + type + retrieval time, so a conflict is
  detectable rather than invisible.
- **Recency as tie-break only within a tier**, never across (a fresh syndicated blog must not outrank a filing).
- Then a cheap deterministic cross-source consistency check can flag "same metric, same period, two values"
  *before* generation, and the prompt can be told which one is authoritative.

## Scope

Tier A only. Tier B (`e2e.py` against `/api/chat/stream`, plus the 24 `golden_queries.jsonl` rows) is not
implemented here — it needs question-service running, and the golden-query set deserves its own runner with
TTFT/p95 measurement and a router confusion matrix.

`report.json` is written into this directory and is not currently gitignored — add `backend/evals/report.json`
to `.gitignore` if you don't want run artifacts tracked.
