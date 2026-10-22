# Cloud Run-ready Dockerfile for the backend (FastAPI BFF).
# Build context is the REPO ROOT (see deploy.sh svc_context), so the image can
# include the shared libs/ packages:
#   docker build -f apps/backend/Dockerfile .
FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching. The shared `skills` package is
# installed from the monorepo checkout, not from git.
COPY libs/skills /libs/skills
COPY apps/backend/requirements.txt .
RUN pip install --no-cache-dir /libs/skills -r requirements.txt

COPY apps/backend/ .

# Cloud Run sets $PORT (default 8080). uvicorn must bind to it.
ENV PORT=8080
EXPOSE 8080

# Use sh -c so $PORT is expanded at runtime
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
