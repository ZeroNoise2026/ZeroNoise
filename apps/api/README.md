# apps/api

The one HTTP service behind the web app (FastAPI, :8000): Supabase auth, user
data (watchlist, preferences, briefings, chat sessions, files), on-demand
summaries, skill routing and the RAG question flow.

> Part of the zeroNoise monorepo. `./dev.sh setup api` builds the venv;
> configuration comes from the single repo-root `.env`.

## Layout

```
apps/api/
├── main.py              routes; /api/chat/stream = skill router → RAG
├── config.py            app settings (loads the repo-root .env)
├── auth.py              Supabase JWT verification
├── db.py                user data: watchlist, preferences, briefings, chat
├── summarizer.py        short on-demand summaries (/api/summarize)
├── files/               upload, parse and preview (pdf/docx/xlsx/csv/txt)
├── migrations/          SQL for user-scoped tables + RLS
├── question/            ── the RAG question flow ──
│   ├── sse.py           ask_sse(): run the flow, render Server-Sent Events
│   ├── orchestrator.py  per-request flow
│   ├── router.py        L1 keyword → L2 Haiku intent classification
│   ├── retriever.py     pgvector search + recency rerank + query expansion
│   ├── live_fetcher.py  untracked tickers: live data via libs/marketdata
│   ├── structured.py    JSON-schema-constrained Claude calls
│   ├── generator.py     free-form answer path
│   ├── tier2_cache.py   in-process LRU for untracked tickers
│   ├── db.py            tracked-ticker bookkeeping, hot-ticker promotion, vector search
│   ├── embedding_client.py  async client for apps/embedding
│   ├── schemas/         per-intent JSON Schema + templates
│   └── templates/       sandboxed Jinja2 engine, filters, functions
├── scripts/             SQL setup + manual dev tools (need live services)
└── tests/
```

## One chat request

`POST /api/chat/stream` persists the user message, then:

1. **Skill routing** — `libs/skills` asks Haiku whether a skill
   (`generate_report`, `compare_tickers`) fits. A match answers directly.
2. **Route** — otherwise `question.router` picks an intent (`EARNINGS_ANALYSIS`,
   `COMPARISON`, `PRICE_QUERY`, `NEWS_SUMMARY`, …). L1 is keyword + regex and
   free; L2 is a small Haiku call, used only when L1 misses or the query also
   warrants expansion.
3. **Retrieve** — tracked tickers: encode via `apps/embedding`, pgvector search
   with recency-aware reranking and optional multi-vector query expansion.
   Untracked tickers: live Finnhub / FMP data via `libs/marketdata`.
4. **Generate** — for `EARNINGS_ANALYSIS` / `COMPARISON` / `PRICE_QUERY`, the
   context is built deterministically from DB rows and Claude writes only the
   narrative slots (structured outputs), rendered through sandboxed Jinja2.
   **Numbers are computed in `templates/functions.py`, never by the LLM.**
   `PRICE_QUERY` skips the LLM entirely. Other intents stream a free-form
   answer from `generator.py`.
5. **Stream** — `question.sse.ask_sse` emits `session`, `status`, `thinking`,
   `token`, `sources` and `clarification` events, keepalives, then `[DONE]`.
   The answer and its citations are saved to `chat_messages`.

## Run

```bash
./dev.sh up api                       # or, by hand from apps/api:
uvicorn main:app --port 8000 --reload

docker build -f apps/api/Dockerfile -t zn-api .     # from the repo root
docker run -p 8000:8080 --env-file .env zn-api
```

## Settings

`config.py` reads (defaults in the repo-root `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `LLM_MODEL` / `LLM_EFFORT` | `claude-haiku-5-5` / `medium` | `/api/summarize` |
| `LLM_MODEL_QA` / `LLM_EFFORT_QA` | `claude-haiku-5-5` / `medium` | chat answers + structured JSON |
| `LLM_MODEL_CLASSIFY` | `claude-haiku-5-5` | L2 router |
| `EMBEDDING_SERVICE_URL` | `http://localhost:8002` | query encoding |
| `SEMANTIC_TOP_K` / `SIMILARITY_THRESHOLD` | `8` / `0.3` | retriever |
| `RECENCY_HALF_LIFE_DAYS` | `14` | recency rerank |
| `EXPANSION_MAX_QUERIES` | `4` | cap on expanded queries |
| `TIER2_CACHE_MAX_SIZE` | `200` | LRU size for untracked tickers |
| `HOT_PROMOTION_THRESHOLD` | `10` | queries/24h before an untracked ticker gets ingested |
| `ALLOWED_ORIGINS` | localhost | CORS in production |

## Tests

```bash
python -m pytest -q tests            # offline: chat stream, question sources, templates
python scripts/test_t4_router.py     # router only, in-memory
python scripts/test_retrieval.py     # needs Supabase + apps/embedding
```
