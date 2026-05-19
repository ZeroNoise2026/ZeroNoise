# 🔢 Embedding Service

> QuantAgent Microservice — Shared Vectorization Service

Encodes text into 384-dimensional vectors (all-MiniLM-L6-v2). Used jointly
by `data-pipeline` and `copilot-template`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/encode` | Batch text → vectors |
| POST | `/api/encode-query` | Single query → vector |
| GET | `/health` | Health check |

## Quick Start

```bash
cp .env.example .env
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8002 --reload
```

## API Examples

```bash
# Batch encoding
curl -X POST http://localhost:8002/api/encode \
  -H "Content-Type: application/json" \
  -d '{"texts": ["Apple revenue grew 15%", "TSLA earnings miss"]}'

# Single query
curl -X POST http://localhost:8002/api/encode-query \
  -H "Content-Type: application/json" \
  -d '{"query": "How was AAPL'"'"'s latest earnings?"}'
```

## Related Services

| Service | Repo | How it calls this service |
|---------|------|---------------------------|
| data-pipeline | [data-pipeline](https://github.com/ZeroNoise2026/data-pipeline) | Encodes documents on ingest |
| copilot-template | [copilot-template](https://github.com/ZeroNoise2026/copilot-template) | Encodes queries at retrieval time |
