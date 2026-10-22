# Cloud Run-ready Dockerfile for embedding-service (FastAPI + sentence-transformers)
FROM python:3.11-slim

WORKDIR /app

# sentence-transformers / torch build transitive deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets $PORT (default 8080). Locally override with -e PORT=8002 if needed.
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
