# embedding-service

> Shared vectorization service for the zeroNoise stack.

Encodes text into 384-dimensional vectors with `all-MiniLM-L6-v2`. Small,
stateless, and the **only service in the stack that needs no external
credentials** — which makes it the first thing worth starting when you are
checking whether your setup works at all.

> Part of the zeroNoise monorepo. Run `./dev.sh` from the repo root — it sets
> up this directory's venv and `.env`.

## Why it is a separate service

Both `data-pipeline` (at ingest time, in batches) and `Summarization/question`
(at query time, one query at a time) need vectors, and both must use **the same
model** or cosine similarity is meaningless. Rather than pin the model in two
places and hope they stay in sync, it lives here once. Changing
`EMBEDDING_MODEL` changes it for everybody, and any change means re-embedding
the whole `documents` table.

## Setup

Handled by `./dev.sh setup embedding` from the repo root. Manually:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --port 8002 --reload
```

**First start downloads the model (~90MB)** from HuggingFace and needs network
access. It is cached afterwards, so subsequent boots are fast. `./dev.sh up`
allows up to 180s for this service's first health check for exactly this reason.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/encode` | Batch: `{"texts": [...]}` → `{"embeddings": [[...], ...]}` |
| POST | `/api/encode-query` | Single: `{"query": "..."}` → `{"embedding": [...]}` |
| GET | `/health` | Liveness |

```bash
curl -X POST http://localhost:8002/api/encode \
  -H "Content-Type: application/json" \
  -d '{"texts": ["Apple revenue grew 15%", "TSLA earnings miss"]}'

curl -X POST http://localhost:8002/api/encode-query \
  -H "Content-Type: application/json" \
  -d '{"query": "How was AAPL latest earnings?"}'
```

Two endpoints rather than one because the callers differ: ingest sends hundreds
of chunks and cares about throughput, retrieval sends one string and cares about
latency.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model name |
| `EMBEDDING_DIM` | `384` | Must match the model **and** the pgvector column width in `data-pipeline/pipeline/schema.sql` |
| `PORT` | `8002` | Listen port |

⚠️ These have real defaults, so do not blank them out in `.env` — `config.py`
reads `int(os.getenv("EMBEDDING_DIM", "384"))`, and a variable that is present
but empty is **not** the default, it is `int("")` and the service will not
start. `./dev.sh` scaffolds `.env` in a way that preserves them.

## Docker

```bash
docker build -t qa-embedding .
docker run -p 8002:8080 --env-file .env qa-embedding
```

Cloud Run ready — honours `$PORT`. Give it enough memory for the model
(512MB is comfortable) and prefer `min-instances=1`, since a cold start pays the
model-load cost on the request path.

## Consumers

| Service | How it calls this one |
|---|---|
| [pipeline](../pipeline) | `POST /api/encode` for every batch of chunks at ingest, 100 per request |
| [summarization](../summarization) (`question`) | `POST /api/encode-query` per user query; also `/api/encode` to batch-encode expanded queries for multi-vector retrieval |

Both reach it via `EMBEDDING_SERVICE_URL`, default `http://localhost:8002`.
Neither has a fallback: if this service is down, ingestion stops and RAG
retrieval fails. That is intentional — silently degrading to keyword search
would produce answers that look fine and cite the wrong documents.

## Testing

```bash
pytest -v
curl -s localhost:8002/health
```
