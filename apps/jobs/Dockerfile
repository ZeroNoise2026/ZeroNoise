# One image for every batch job. Build context is the REPO ROOT so the image
# can install the shared libs/ packages:
#   docker build -f apps/jobs/Dockerfile -t zn-jobs .
#   docker run --env-file .env zn-jobs python -m pipeline.run       # ingestion
#   docker run --env-file .env zn-jobs python -m reports.run --all  # reports
#   docker run --env-file .env zn-jobs python -m briefing           # briefings
#   docker run --env-file .env zn-jobs python -m reports.cleanup    # cache retention
#   docker run ... zn-jobs prefect worker start --pool <pool>       # Prefect Cloud worker
FROM python:3.11-slim

WORKDIR /app

COPY libs/common /libs/common
COPY libs/research /libs/research
COPY libs/skills /libs/skills
COPY libs/marketdata /libs/marketdata
COPY apps/jobs/requirements.txt .
RUN pip install --no-cache-dir /libs/common /libs/research /libs/skills /libs/marketdata -r requirements.txt

COPY apps/jobs/ .
RUN mkdir -p /app/output

CMD ["python", "-m", "reports.run", "--all"]
