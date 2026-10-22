# Deployment

`dev.sh` runs the stack on your laptop. `deploy.sh` runs it on GCP, from the
same monorepo, the same `.env` and the same service registry.

```bash
./deploy.sh preflight     # what is ready, what is blocked, and why
./deploy.sh bootstrap     # once per project
./deploy.sh secrets       # whenever a credential changes
./deploy.sh deploy        # every time you ship
```

## What lands where

| Service | Cloud Run name | Source | Dockerfile |
|---|---|---|---|
| embedding | `qa-embedding` | `apps/embedding` | `Dockerfile` |
| question | `qa-question` | `apps/summarization` | `Dockerfile.question` |
| backend | `qa-backend` | repo root | `apps/backend/Dockerfile` |
| pipeline *(opt-in)* | `qa-pipeline` | `apps/pipeline` | `Dockerfile.api` |
| frontend | — | `apps/web` | Vercel, see below |

`deploy` without arguments does `embedding question backend`. `pipeline` is
opt-in (`./deploy.sh deploy pipeline`) because the live-lookup API is only
needed once ingestion is running in the cloud.

## First time on a new project

```bash
gcloud auth login
gcloud config set project <your-project-id>

./deploy.sh bootstrap     # APIs, Artifact Registry, one service account per service
./deploy.sh secrets       # reads the repo-root .env, writes Secret Manager
./deploy.sh deploy
./deploy.sh frontend      # prints the Vercel side
```

Region defaults to `us-central1`. Override per-invocation with
`DEPLOY_REGION=europe-west1 ./deploy.sh …`, or permanently by creating
`.deployrc` (gitignored) next to `deploy.sh`:

```bash
DEPLOY_PROJECT=quantagent-prod
DEPLOY_REGION=us-central1
```

## The config model

Three kinds of configuration, kept deliberately separate:

**Secrets** live in Secret Manager and are mounted at runtime — they never
appear in a revision spec, so `gcloud run services describe` is safe to paste
into a chat. `./deploy.sh secrets` reads the authoritative value from the
repo-root `.env` and adds a new version only when it actually changed.

| Secret | Used by |
|---|---|
| `SUPABASE_KEY` | backend, question, pipeline |
| `SUPABASE_JWT_SECRET` | backend |
| `ANTHROPIC_API_KEY` | backend, question |
| `FINNHUB_API_KEY` `FMP_API_KEY` | pipeline |

One secret, many consumers, one local file. On a new machine,
`./dev.sh env pull` fills `.env` back in from Secret Manager.

**Plain config** (`SUPABASE_URL`, model names, `ALLOWED_ORIGINS`, …) is copied
verbatim from the repo-root `.env` at deploy time.

**Service URLs** are never written down. `EMBEDDING_SERVICE_URL`,
`QUESTION_SERVICE_URL` and `DATA_PIPELINE_URL` are resolved from what is
actually live, in dependency order — embedding → pipeline → question → backend.
A service physically cannot point at a stale URL.

> `deploy` uses `--set-env-vars`, which **replaces** the whole env block. That
> is intentional: the revision matches your `.env`, with no invisible leftovers
> from a manual `gcloud run services update`. The corollary is that anything you
> set by hand is gone on the next deploy — put it in `.env` instead.

## Images and traceability

Every image is tagged with the monorepo's git SHA, so a Cloud Run
revision always maps back to a commit. A dirty working tree gets a `-dirty`
suffix and a warning at preflight: you can ship it, you just cannot reproduce
it.

The backend is the exception worth understanding. Its build context is the
**repo root**, not `apps/backend/`, so the image can install `libs/skills`
from the same checkout — what you test locally is exactly what ships. The
root `.dockerignore` keeps venvs, `.env` files and `apps/web` out of that
context.

## Frontend (Vercel)

The frontend is a static Vite build and is not deployed by `deploy.sh`.

```bash
./deploy.sh frontend                          # prints the backend URL to paste

cd frontend
vercel env add VITE_API_URL production        # the qa-backend URL
vercel --prod

./deploy.sh cors https://<your-app>.vercel.app
```

Two directions to wire, and both are easy to forget:

- **Vercel → backend**: `VITE_API_URL`. Baked in at *build* time, so changing it
  needs a redeploy, not a restart.
- **backend → Vercel**: `ALLOWED_ORIGINS`. `./deploy.sh cors` sets it plus a
  regex for preview deploys. Mirror it into `.env` so the next full
  deploy does not drop it.

## Rolling back

```bash
./deploy.sh status              # revisions, newest first
./deploy.sh rollback backend    # 100% traffic to the previous revision
```

Rollback is traffic-only and takes effect in seconds — it does not rebuild
anything. Note it does not roll back *config*: if the bad deploy also changed an
env var, redeploy from the previous commit instead.

## Cost notes

Every service is deployed with `--min-instances 0`. A warm instance costs about
$12/month whether or not anyone uses it, and this stack has no latency SLO that
justifies that. The price is a cold start, which for `qa-embedding` is real —
it loads `all-MiniLM-L6-v2` on boot, and the model is downloaded from
HuggingFace on first request rather than baked into the image.

`--max-instances` is set low (3 for embedding, 5 for the rest). These services
are `--allow-unauthenticated`, so max-instances is the blast radius if a URL is
ever found and hammered.

## Known gaps

- **`qa-embedding` and `qa-question` are publicly reachable.** Making them
  private needs the callers to attach an ID token, which is a code change in
  `data-pipeline/pipeline/run.py` and `Summarization/question/retriever.py`.
  Until then, `--max-instances` is the only thing bounding the damage.
- **The embedding image is much larger than it needs to be.** `sentence-transformers`
  pulls the default PyPI `torch`, which is the CUDA build; Cloud Run has no GPU.
  Installing from `https://download.pytorch.org/whl/cpu` and baking the model in
  at build time would cut the image by an order of magnitude and take the cold
  start from ~60s to a few seconds.
- **The daily briefing is not running anywhere.** There is no workflow for
  `apps/backend/briefing.py`; it needs an hourly `.github/workflows/` job or a
  Cloud Scheduler → Cloud Run Job.
- **Ingestion is not scheduled in the cloud.** `.github/workflows/pipeline-ingest.yml`
  is `workflow_dispatch` only.
