# Cloud Run-ready Dockerfile for the backend (FastAPI BFF).
# Build context is the REPO ROOT (see deploy.sh svc_context), so the image can
# include the shared libs/ packages:
#   docker build -f apps/backend/Dockerfile .
FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching. The shared libs/ packages are
# installed from the monorepo checkout.
COPY libs/common /libs/common
COPY libs/research /libs/research
COPY libs/skills /libs/skills
COPY apps/backend/requirements.txt .
RUN pip install --no-cache-dir /libs/common /libs/research /libs/skills -r requirements.txt

COPY apps/backend/ .

# Cloud Run sets $PORT (default 8080). uvicorn must bind to it.
ENV PORT=8080
EXPOSE 8080

# Use sh -c so $PORT is expanded at runtime
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
