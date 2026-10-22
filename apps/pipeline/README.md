# data-pipeline

> Data ingestion, cleaning, embedding and storage for the QuantAgent stack.

Pulls raw financial data from Finnhub, SEC EDGAR and FMP; cleans and chunks it;
calls `embedding-service` to vectorize; writes to Supabase. Also exposes a small
FastAPI layer for ad-hoc live lookups the other services hit directly.

> Part of the [QuantAgent](https://github.com/ZeroNoise2026/QuantAgent) stack.
> You do not clone this repo directly: clone QuantAgent and run `./dev.sh`,
> which pulls this one as a sibling and sets up its venv and `.env`.

> **For PMs / analysts:** this is the upstream pipeline that keeps Supabase
> fresh. Everything users see in chat, briefings or research reports is fetched,
> cleaned and indexed here first. If chat says "no recent data for this ticker",
> the answer almost always lives in this repo — was it added to
> `tracked_tickers`? did the Finnhub/FMP quota run out? did the embedder fail?

> **For engineers:** two artifacts — a CLI / Prefect flow (`pipeline/run.py`)
> for scheduled ingestion, and a FastAPI layer (`pipeline/api.py`) for sync live
> lookups used by Summarization's question-service. We are the **only writer**
> for `documents`, `earnings`, `price_snapshot`, `tracked_tickers` and
> `summary_cache`.

## Architecture

```
   ┌─ Finnhub API ─┐
   ├─ SEC EDGAR ───┤  pipeline/clients/*
   └─ FMP API ─────┘
           │
   pipeline/cleaner.py       normalize, dedupe, strip boilerplate
           │
   pipeline/chunker.py       512-char chunks, 64-char overlap, sentence-aware
           │
   embedding-service /encode 384-dim all-MiniLM-L6-v2 vectors (batched)
           │
   pipeline/store.py         upsert into Supabase, idempotent by content hash
           │
   ┌─ documents ─ earnings ─ price_snapshot ─ tracked_tickers ─┐
   └─────────────── Supabase (pgvector) ───────────────────────┘
           ▲  read-only from Summarization + QuantAgent
```

## Layout

```
data-pipeline/
├── pipeline/
│   ├── run.py                Prefect @flow entry — full ingestion pass
│   ├── api.py                FastAPI :8001 — live lookups
│   ├── config.py             env-driven config
│   ├── cleaner.py            normalize, strip boilerplate, dedupe
│   ├── chunker.py            512-char chunks with overlap
│   ├── store.py              Supabase writer (upsert by content SHA256)
│   ├── schema.sql            DDL for EVERY table in the project
│   ├── clients/              finnhub_client, edgar_client, fmp_client
│   └── utils/retry.py
├── Dockerfile.api            FastAPI image (Cloud Run ready, $PORT)
├── Dockerfile.worker         Prefect worker image
└── .github/workflows/ingest.yml
```

## Setup

Handled by `./dev.sh setup pipeline` from QuantAgent. Manually:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
psql "$SUPABASE_DB_URL" -f pipeline/schema.sql   # one-time
```

Prerequisites: Python 3.11+, Supabase with `pgvector` enabled, a reachable
embedding-service on :8002, and API keys for Finnhub and FMP plus an
`EDGAR_USER_AGENT` (SEC requires a contact email in the UA).

## Ingestion

```bash
python -m pipeline.run                  # every active ticker
python -m pipeline.run --ticker AAPL    # single ticker
python -m pipeline.run --dry-run        # log everything, write nothing
python -m pipeline.run --initial        # backfill windows instead of incremental
```

Per-ticker behaviour: failures are isolated per ticker; 1s sleep between tickers
for Finnhub's 60 req/min limit; embeddings batched 100 chunks per request; all
writes are upserts by content SHA256, so re-running is safe.

## Live-lookup API

```bash
uvicorn pipeline.api:app --port 8001 --reload
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/api/finnhub/news/{ticker}` | Live news |
| GET | `/api/fmp/quote/{ticker}` | Live quote |
| GET | `/api/finnhub/earnings/{ticker}` | Live earnings |

Read-only, consumed by services that need *live* data rather than cached
snapshots.

## Orchestration

Ingestion is wrapped in Prefect `@flow` / `@task` for retry and observability.

- **Local:** `python -m pipeline.run` works without a Prefect server — the
  decorators just add logging and retries.
- **With a server:** `prefect server start`, then deploy from the UI at :4200.
- **Production:** `Dockerfile.worker` runs `prefect worker start`, intended as a
  long-lived Cloud Run service (`min-instances=1`) pulling from a Prefect Cloud
  work pool. Needs `PREFECT_API_URL`, `PREFECT_API_KEY`, `PREFECT_WORK_POOL`.

`.github/workflows/ingest.yml` is an alternative cron path, currently
`workflow_dispatch` only — uncomment the `schedule: '0 */6 * * *'` to enable.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | yes | Supabase credentials |
| `EMBEDDING_SERVICE_URL` | yes | e.g. `http://localhost:8002` |
| `FINNHUB_API_KEY` | yes | Finnhub Plus or higher (news + earnings) |
| `FMP_API_KEY` | yes | Financial Modeling Prep |
| `EDGAR_USER_AGENT` | yes | SEC requires a contact email |
| `DEFAULT_TICKERS` | no | Seed list |
| `PREFECT_*` | worker only | Prefect Cloud config |

## Schema

`pipeline/schema.sql` is the **single source of truth** for the DDL of every
table in the project, not just the ones we write.

- **Owned here (we are the writer):** `documents`, `earnings`, `price_snapshot`,
  `tracked_tickers`, `summary_cache` (written by Summarization, DDL lives here).
- **Owned elsewhere:** `user_watchlist`, `user_preferences`, `daily_briefings`,
  `chat_sessions`, `chat_messages` — QuantAgent is the writer; the DDL lives
  here to keep the schema in one place.

`documents.id` is a SHA256 of the cleaned chunk text, so identical content always
maps to the same id. Summarization's cache key relies on that stability, and the
eval suite relies on it for reproducible source ids.

## Skills integration

This repo does **not** call into `Skills`. Ingestion is upstream of any
user-facing capability; the flow direction is strictly:

```
data-pipeline → Supabase → {Summarization, QuantAgent, Skills}
```

That said, the schema choices here — especially `documents.id = SHA256(content)`
and the cache lineage in `summary_cache` — are what make skill behaviour
reproducible. Changing the DDL means follow-up work in
`Summarization/summary/cache.py`.

## Related services

| Service | Why this repo cares |
|---|---|
| `embedding-service` (:8002) | We POST every chunk batch to `/api/encode` |
| `Summarization` | Reads `documents`, `earnings`, `price_snapshot`; writes only `summary_cache` |
| `QuantAgent` | Reads the same tables; writes only per-user tables |
