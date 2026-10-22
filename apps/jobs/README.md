# apps/jobs

Every batch job in one place: no HTTP server, one image, one entry point per
job. Schedule them with GitHub Actions (`.github/workflows/jobs-*.yml`) or any
cron runner.

> Part of the zeroNoise monorepo. `./dev.sh setup jobs` builds the venv;
> configuration comes from the single repo-root `.env`.

| Job | Run | What it does | Schedule |
|---|---|---|---|
| ingest | `python -m pipeline.run` | Finnhub / SEC EDGAR / FMP → clean → chunk → embed (`apps/embedding`) → Supabase | `jobs-ingest.yml` (manual; cron commented out) |
| reports | `python -m reports.run --all` | Research report per tracked ticker via `research.report.build_report` (cached by input hash) → `output/` | on demand |
| briefing | `python -m briefing` | Per-user daily watchlist briefing at 08:00 local time via `research.briefing` | `jobs-briefing.yml` (hourly; cron commented out) |
| cleanup | `python -m reports.cleanup` | Delete `summary_cache` rows older than 90 days | `jobs-report-cache-cleanup.yml` (daily, 09:00 UTC) |

Locally: `./dev.sh job ingest|reports|briefing|cleanup [args]` from the repo
root. Ingestion needs `apps/embedding` running (`./dev.sh up embedding`).

## Layout

```
apps/jobs/
├── config.py            report/briefing settings (loads the repo-root .env)
├── briefing.py          daily briefings
├── reports/
│   ├── run.py           reports CLI (also runs any skill: --skill / --list-skills)
│   └── cleanup.py       summary_cache retention
├── pipeline/            ── ingestion ──
│   ├── run.py           Prefect @flow entry — full ingestion pass
│   ├── config.py        batch sizes, refresh windows, Supabase
│   ├── cleaner.py       normalize, strip boilerplate, dedupe
│   ├── chunker.py       512-char chunks, 64-char overlap, sentence-aware
│   ├── store.py         Supabase writer (upsert by content SHA256)
│   └── schema.sql       DDL for EVERY table in the project
├── Dockerfile           one image for all jobs (build from the repo root)
└── tests/
```

Market-data clients (Finnhub, FMP, EDGAR) live in `libs/marketdata`; report
and briefing generation in `libs/research`.

## Ingestion

```bash
python -m pipeline.run                  # every active ticker
python -m pipeline.run --ticker AAPL    # single ticker
python -m pipeline.run --dry-run        # log everything, write nothing
python -m pipeline.run --initial        # first-time deep fetch, ignore skip logic
```

Failures are isolated per ticker; a 1s sleep between tickers respects
Finnhub's 60 req/min; embeddings are batched 100 chunks per request; all writes
are upserts by content SHA256, so re-running is safe.

Ingestion is wrapped in Prefect `@flow` / `@task` for retries and logging.
Locally it needs no Prefect server. For Prefect Cloud, run the jobs image with
`prefect worker start --pool <pool>` and set `PREFECT_API_URL` /
`PREFECT_API_KEY`.

## Reports

```bash
python -m reports.run --ticker AAPL,MSFT     # cached by input hash
python -m reports.run --all --dry-run        # assemble context only
python -m reports.run --list-skills
python -m reports.run --skill compare_tickers --args '{"tickers":["AAPL","MSFT"]}'
```

## Schema

`pipeline/schema.sql` is the single source of truth for every table's DDL.
Ingestion is the only writer of `documents`, `earnings`, `price_snapshot` and
`tracked_tickers`. `documents.id` is a SHA256 of the cleaned chunk text, so
identical content always maps to the same id — the report cache key
(`libs/research`) relies on that stability.

## Docker

```bash
docker build -f apps/jobs/Dockerfile -t zn-jobs .          # from the repo root
docker run --env-file .env zn-jobs python -m briefing
```
