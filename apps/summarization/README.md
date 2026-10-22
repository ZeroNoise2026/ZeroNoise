# Summarization

Two co-located entry points: the `summary` report CLI and the `question` RAG
service.

> Part of the zeroNoise monorepo. Run `./dev.sh` from the repo root — it sets
> up this directory's venv and `.env`.

| Service | Form | Purpose |
|---|---|---|
| **summary** | CLI / scheduled job | Generates a Markdown research report per ticker from Supabase + Claude. Caches by input hash, so unchanged inputs reuse the previous report. |
| **question** | FastAPI service (:8003) | RAG-based ad-hoc Q&A: structured-output intents (earnings, comparison, price) plus a templated rendering layer that keeps numbers deterministic and lets the LLM write only the prose. |

> **For PMs / analysts:** this is where the deep analysis lives. `summary` is the
> engine behind the daily research report for any watchlist ticker. `question`
> is the engine behind chat answers like "how was AAPL's last quarter" — it
> makes sure the numbers come from the database, not the model's memory.

> **For engineers:** `summary/` is a thin CLI over `research.report.build_report`
> (libs/research); `question/` is a FastAPI app. Both use `config.py` and
> `shared/` (question-owned DB queries, HTTP clients); the Claude client,
> Supabase client and audit log come from libs/common.

## Architecture

```
        Supabase (documents, earnings, price_snapshot, summary_cache)
                          ▲          ▲
       ┌──────────────────┘          └──────────────────┐
   summary/                                        question/
   ┌──────────────────────┐                  ┌──────────────────────┐
   │ run.py (CLI)         │                  │ FastAPI :8003        │
   │  └─ research.report  │                  │  /api/ask/stream     │
   │     .build_report()  │                  │ router → orchestrator│
   │     → output/        │                  │   ├─ retriever (RAG) │
   └──────────────────────┘                  │   ├─ live_fetcher    │
                                             │   ├─ structured      │
                                             │   └─ templates/      │
                                             └──────────────────────┘
```

## Layout

```
apps/summarization/
├── config.py                  app settings (loads .env)
├── summary/                   ── report CLI ──
│   ├── run.py                 python -m summary.run (reports or --skill)
│   └── cleanup.py             summary_cache retention sweep
├── question/                  ── online Q&A service ──
│   ├── main.py                FastAPI app (:8003)
│   ├── orchestrator.py        per-request flow
│   ├── router.py              L1 keyword → L2 LLM intent classification
│   ├── retriever.py           pgvector search + recency rerank + query expansion
│   ├── live_fetcher.py        real-time quote fallback
│   ├── structured.py          JSON-schema-constrained LLM calls
│   ├── tier2_cache.py         in-process LRU for untracked tickers (hot-ticker promotion counts in Postgres: scripts/setup_ticker_queries.sql)
│   ├── generator.py           free-form answer path
│   ├── schemas/               per-intent JSON Schema + templates
│   └── templates/             sandboxed Jinja2 engine, filters, functions
├── shared/                    db.py (question-owned queries), http.py
├── scripts/                   SQL + dev tools
└── output/                    {TICKER}_{YYYY-MM-DD}.md (gitignored)
```

Report logic (context, prompt, cache) lives in `libs/research`; the Claude
client in `libs/common`.

## Setup

Handled by `./dev.sh setup question` from the repo root. Manually:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -e ../../libs/common -e ../../libs/research -e ../../libs/skills
cp .env.example .env        # then fill in credentials
```

Prerequisites: Python 3.11+, a Supabase project with
`apps/pipeline/pipeline/schema.sql` applied and `pgvector` enabled, a
Anthropic API key, and a reachable embedding service on :8002.

## summary — CLI

```bash
python -m summary.run --ticker AAPL
python -m summary.run --ticker AAPL,MSFT,NVDA
python -m summary.run --all                    # every active ticker (the cron path)
python -m summary.run --ticker AAPL --dry-run  # assemble context, skip the LLM
python -m summary.run --list-skills
python -m summary.run --skill generate_report --args ticker=AAPL
python -m summary.run --skill compare_tickers --args '{"tickers":["AAPL","MSFT"]}'
```

Reports land in `output/{TICKER}_{DATE}.md`. The cache key is
`SHA256(sorted source doc ids + model + prompt_version)` — unchanged inputs mean
no LLM call.

## question — service

```bash
uvicorn question.main:app --port 8003 --reload

docker build -f apps/summarization/Dockerfile.question -t qa-question ../..   # repo-root context
docker run -p 8003:8080 --env-file .env qa-question
```

| Method | Path | Description |
|---|---|---|
| POST | `/api/ask/stream` | SSE stream of tokens + structured events (`status`, `sources`, `clarification`) |
| GET | `/health` | Liveness |

Consumed by `apps/backend/rag.py`.

## Data flow — summary

All in `research.report.build_report(ticker)`:

1. **Fetch** — `research.context.fetch_context` pulls documents, earnings rows
   and price snapshots into a `TickerContext`. Over `MAX_CONTEXT_CHARS`, news
   and filings truncate first; earnings and price stay intact.
2. **Cache check** — `compute_input_hash` over source doc ids + model + prompt
   version. Hit → reuse.
3. **Generate** — Claude (`LLM_MODEL_SUMMARY`) with retry, then `summary_cache`.
   The CLI saves the Markdown to `output/`.

## Data flow — question

1. **Route** — `router.classify()` picks an intent (`EARNINGS_ANALYSIS`,
   `COMPARISON`, `PRICE_QUERY`, `NEWS_SUMMARY`, …). L1 is keyword + regex and
   free; L2 is a small Haiku call, used only when L1 misses or when the query
   also warrants expansion.
2. **Retrieve** — encode via embedding-service, pgvector search with
   recency-aware reranking and optional multi-vector query expansion.
3. **Structured generation** — for `EARNINGS_ANALYSIS` / `COMPARISON` /
   `PRICE_QUERY`, build context deterministically from DB rows, ask Claude for
   only the narrative slots (structured outputs), render through sandboxed
   Jinja2. **Numbers are computed in `templates/functions.py`, never by the LLM.**
4. **Free-form fallback** — other intents go through `generator.py`.
5. **Stream** — tokens and structured events as SSE.

`PRICE_QUERY` skips the LLM entirely and renders from DB + live quote.

## Configuration

Everything lives in `config.py`. Required: `SUPABASE_URL`, `SUPABASE_KEY`,
`ANTHROPIC_API_KEY`, `EMBEDDING_SERVICE_URL`.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_MODEL_SUMMARY` | `claude-sonnet-5-5` | Report model |
| `LLM_MODEL_QA` | `claude-haiku-5-5` | Q&A stream + structured JSON |
| `LLM_MODEL_CLASSIFY` | `claude-haiku-5-5` | L2 router model |
| `LLM_EFFORT_QA` | `medium` | Q&A thinking depth (`low` for lowest latency) |
| `MAX_CONTEXT_CHARS` | `380000` | Per-ticker context budget |
| `SEMANTIC_TOP_K` | `8` | Retriever top-K |
| `SIMILARITY_THRESHOLD` | `0.3` | Cosine threshold |
| `RECENCY_HALF_LIFE_DAYS` | `14` | Recency-rerank half-life |
| `EXPANSION_MAX_QUERIES` | `4` | Cap on expanded queries |
| `TIER2_CACHE_MAX_SIZE` | `200` | LRU size for untracked tickers |

## Skills integration

`summary/run.py --skill <name>` runs any skill from `libs/skills`.
`question/` does not call skills — it stays the RAG / structured-intent
service. Chat-side skill routing lives in `apps/backend/main.py`.

`generate_report` and the `--ticker` CLI path both call
`research.report.build_report`, so "skill in chat" and "CLI report" stay in
lockstep.

## Database tables

| Table | Used by | Purpose |
|---|---|---|
| `documents` | both | News, 10-K/10-Q, earnings chunks with embeddings |
| `earnings` | both | Quarterly EPS, revenue, net income, guidance |
| `price_snapshot` | both | Daily close, P/E, market cap |
| `tracked_tickers` | summary | Active ticker list (`--all`) |
| `summary_cache` | summary | Cached reports by input_hash |
| `audit_log` | both | Structured query/response logging |

We never write to the first four — `apps/pipeline` owns them.

## Testing

```bash
pytest tests/ -v
pytest tests/test_templates_phase1.py -v   # fast, no services
python scripts/test_retrieval.py           # needs Supabase + embedding-service
python scripts/test_t4_router.py           # router only, pure in-memory
```

## Related services

| Service | Why this app cares |
|---|---|
| `apps/embedding` (:8002) | `question/` calls it to encode queries before pgvector search |
| `apps/pipeline` | Populates the tables we read. We never write to them. |
| `apps/backend` | Calls `question/` over HTTP |
