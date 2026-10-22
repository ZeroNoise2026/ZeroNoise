# Summarization

Two co-located services sharing one data layer and one LLM client.

> Part of the QuantAgent monorepo. Run `./dev.sh` from the repo root — it sets
> up this directory's venv and `.env`.

| Service | Form | Purpose |
|---|---|---|
| **summary** | CLI / scheduled job | Generates a Markdown research report per ticker from Supabase + Claude. Caches by input hash, so unchanged inputs reuse the previous report. |
| **question** | FastAPI service (:8003) | RAG-based ad-hoc Q&A: structured-output intents (earnings, comparison, price) plus a templated rendering layer that keeps numbers deterministic and lets the LLM write only the prose. |

> **For PMs / analysts:** this is where the deep analysis lives. `summary` is the
> engine behind the daily research report for any watchlist ticker. `question`
> is the engine behind chat answers like "how was AAPL's last quarter" — it
> makes sure the numbers come from the database, not the model's memory.

> **For engineers:** two services, one repo, one venv, one config. `summary/` is
> a CLI, `question/` is a FastAPI app. They share `config.py`, `shared/` (LLM
> client, DB access, formatters) and `audit/`.

## Architecture

```
        Supabase (documents, earnings, price_snapshot, summary_cache)
                          ▲          ▲
       ┌──────────────────┘          └──────────────────┐
   summary/                                        question/
   ┌──────────────────────┐                  ┌──────────────────────┐
   │ fetcher → prompts →  │                  │ FastAPI :8003        │
   │ summarizer (Claude)  │                  │  /api/ask/stream     │
   │ → cache → output/    │                  │ router → orchestrator│
   └──────────────────────┘                  │   ├─ retriever (RAG) │
                                             │   ├─ live_fetcher    │
                                             │   ├─ structured      │
                                             │   └─ templates/      │
                                             └──────────────────────┘
```

## Layout

```
Summarization/
├── config.py                  single source of truth for env vars
├── summary/                   ── offline report service ──
│   ├── run.py                 CLI entry (python -m summary.run)
│   ├── fetcher.py             assemble TickerContext from Supabase
│   ├── prompts.py             SYSTEM_PROMPT, build_user_prompt, PROMPT_VERSION
│   ├── summarizer.py          Claude call wrapper
│   ├── cache.py               summary_cache integration (input_hash)
│   └── cleanup.py             retention sweep
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
├── shared/                    db.py, llm.py, http.py, formatters.py
├── audit/                     structured logging to Supabase
├── scripts/                   SQL + dev tools
└── output/                    {TICKER}_{YYYY-MM-DD}.md (gitignored)
```

## Setup

Handled by `./dev.sh setup question` from QuantAgent. Manually:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in credentials
```

Prerequisites: Python 3.11+, a Supabase project with
`apps/pipeline/pipeline/schema.sql` applied and `pgvector` enabled, a
Anthropic API key, and a reachable embedding-service on :8002.

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

docker build -f Dockerfile.question -t qa-question .
docker run -p 8003:8080 --env-file .env qa-question
```

| Method | Path | Description |
|---|---|---|
| POST | `/api/ask/stream` | SSE stream of tokens + structured events (`status`, `sources`, `clarification`) |
| GET | `/health` | Liveness |

Consumed by `QuantAgent/backend/rag.py`.

## Data flow — summary

1. **Fetch** — `fetcher.fetch_context(ticker)` pulls documents, earnings rows
   and price snapshots.
2. **Format** — organised into a `TickerContext`. Over `MAX_CONTEXT_CHARS`, news
   and filings truncate first; earnings and price stay intact.
3. **Cache check** — `compute_input_hash` over source doc ids + model + prompt
   version. Hit → reuse.
4. **Prompt → generate → save** — Claude with retry, then `output/` and
   `summary_cache`.

## Data flow — question

1. **Route** — `router.classify()` picks an intent (`EARNINGS_ANALYSIS`,
   `COMPARISON`, `PRICE_QUERY`, `NEWS_SUMMARY`, …). L1 is keyword + regex and
   free; L2 is a small Haiku call, used only when L1 misses or when the query
   also warrants expansion.
2. **Retrieve** — encode via embedding-service, pgvector search with
   recency-aware reranking and optional multi-vector query expansion.
3. **Structured generation** — for `EARNINGS_ANALYSIS` / `COMPARISON` /
   `PRICE_QUERY`, build context deterministically from DB rows, ask Claude for
   only the narrative slots (`json_object` mode), render through sandboxed
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

`summary/run.py` supports `--skill <name>` to route through the shared
[Skills](../../libs/skills) framework; the legacy
`--ticker` path is preserved verbatim. It locates the package by probing
`<workspace>/Skills` and falling back to the pip-installed distribution.

`question/` does not call skills — it stays the RAG / structured-intent service.
Chat-side skill routing lives in `QuantAgent/backend/main.py`.

The shipped skills call back into this repo's `summary.fetcher` and
`summary.summarizer`, so "skill in chat" and "CLI report" stay in lockstep.

## Database tables

| Table | Used by | Purpose |
|---|---|---|
| `documents` | both | News, 10-K/10-Q, earnings chunks with embeddings |
| `earnings` | both | Quarterly EPS, revenue, net income, guidance |
| `price_snapshot` | both | Daily close, P/E, market cap |
| `tracked_tickers` | summary | Active ticker list (`--all`) |
| `summary_cache` | summary | Cached reports by input_hash |
| `audit_log` | both | Structured query/response logging |

We never write to the first four — `data-pipeline` owns them.

## Testing

```bash
pytest tests/ -v
pytest tests/test_templates_phase1.py -v   # fast, no services
python scripts/test_retrieval.py           # needs Supabase + embedding-service
python scripts/test_t4_router.py           # router only, pure in-memory
```

## Related services

| Service | Why this repo cares |
|---|---|
| `embedding-service` (:8002) | `question/` calls it to encode queries before pgvector search |
| `data-pipeline` | Populates the tables we read. We never write to them. |
| `QuantAgent/backend` | Calls `question/` over HTTP and embeds skill execution in its chat stream |
| `Skills` | Hosts `generate_report` and `compare_tickers`, which reuse our fetcher + summarizer |
