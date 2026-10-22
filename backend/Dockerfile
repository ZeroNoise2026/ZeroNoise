# Cloud Run-ready Dockerfile for the QuantAgent backend (FastAPI BFF)
FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching.
# NOTE: this also pulls the shared `skills` package straight from its own git
# repo (see requirements.txt). It deliberately is not COPY'd — the build
# context is backend/, which cannot reach the sibling Skills repo.
# git is required: requirements.txt installs the shared `skills` package from
# its own repo, and pip shells out to git for VCS requirements. python:3.11-slim
# does not ship git, so without this the build dies on the pip step. Removed in
# the same layer so it never reaches the final image.
COPY requirements.txt .
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y --auto-remove git \
 && rm -rf /var/lib/apt/lists/*

COPY . .

# Cloud Run sets $PORT (default 8080). uvicorn must bind to it.
ENV PORT=8080
EXPOSE 8080

# Use sh -c so $PORT is expanded at runtime
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
