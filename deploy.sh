#!/usr/bin/env bash
#
# deploy.sh — single entry point for deploying the QuantAgent stack to GCP.
#
# dev.sh runs the stack on your laptop. This runs it on Cloud Run, from the
# same monorepo, the same .env files and the same service registry — so there is
# exactly one place that knows what a service is called, what it needs, and
# how big it should be.
#
#   ./deploy.sh                     preflight + deploy everything (idempotent)
#   ./deploy.sh preflight           what is ready, what is blocked, and why
#   ./deploy.sh bootstrap           ONE TIME: enable APIs, registry, service accounts
#   ./deploy.sh secrets [svc...]    push secret values from local .env to Secret Manager
#   ./deploy.sh deploy [svc...]     build + deploy, in dependency order, wiring URLs
#   ./deploy.sh urls                print the live service URLs
#   ./deploy.sh status              revisions and traffic
#   ./deploy.sh rollback <svc>      send 100% traffic back to the previous revision
#   ./deploy.sh logs <svc>          tail Cloud Run logs
#   ./deploy.sh cors <origin>       point the backend's CORS at your Vercel domain
#   ./deploy.sh frontend            what to run on the Vercel side
#
# cloud services: embedding question backend pipeline
#   (frontend lives on Vercel — see `./deploy.sh frontend`)
#
# Config, in order of precedence: environment > .deployrc > gcloud defaults
#
#   DEPLOY_PROJECT   GCP project id       (default: gcloud config get-value project)
#   DEPLOY_REGION    Cloud Run region     (default: us-central1)
#   AR_REPO          Artifact Registry    (default: quantagent)
#
# Written for bash 3.2 (what macOS still ships) — no associative arrays.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$ROOT/.deploy"
URLS_FILE="$STATE/urls.env"
RC_FILE="$ROOT/.deployrc"

[ -f "$RC_FILE" ] && . "$RC_FILE"

REGION="${DEPLOY_REGION:-us-central1}"
AR_REPO="${AR_REPO:-quantagent}"
CLOUD_SERVICES="embedding question backend"      # pipeline is opt-in, see below
ALL_CLOUD_SERVICES="embedding pipeline question backend"

# ─────────────────────────────────────────────────────────────
# output  (same vocabulary as dev.sh, on purpose)
# ─────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
else
  C_RESET=""; C_DIM=""; C_BOLD=""; C_RED=""; C_GRN=""; C_YEL=""
fi
say()   { printf '%s\n' "$*"; }
head1() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }
ok()    { printf '  %s✓%s %s\n' "$C_GRN" "$C_RESET" "$*"; }
warn()  { printf '  %s!%s %s\n' "$C_YEL" "$C_RESET" "$*"; }
bad()   { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; }
info()  { printf '  %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }
die()   { printf '\n%serror:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────
# service registry
# ─────────────────────────────────────────────────────────────
svc_dir() {
  case "$1" in
    embedding) echo "$ROOT/apps/embedding" ;;
    pipeline)  echo "$ROOT/apps/pipeline" ;;
    question)  echo "$ROOT/apps/summarization" ;;
    backend)   echo "$ROOT/apps/backend" ;;
    *) return 1 ;;
  esac
}
# Docker build context. backend builds from the repo root so the image can
# COPY libs/ (shared code lives outside the app directory); the rest build from
# their own app directory.
svc_context() {
  case "$1" in
    backend) echo "$ROOT" ;;
    *)       svc_dir "$1" ;;
  esac
}
# `gcloud run deploy --source .` only ever looks for a file named "Dockerfile".
# apps/summarization has two images, so we always build explicitly.
svc_dockerfile() {
  case "$1" in
    embedding) echo "Dockerfile" ;;
    pipeline)  echo "Dockerfile.api" ;;
    question)  echo "Dockerfile.question" ;;
    backend)   echo "apps/backend/Dockerfile" ;;   # relative to svc_context
  esac
}
svc_run_name() { echo "qa-$1"; }
svc_sa_id()    { echo "qa-$1"; }
svc_label() {
  case "$1" in
    embedding) echo "embedding-service (vectors)" ;;
    pipeline)  echo "data-pipeline API (live lookups)" ;;
    question)  echo "question-service (RAG Q&A)" ;;
    backend)   echo "QuantAgent backend (BFF)" ;;
  esac
}
# Secrets pulled from Secret Manager at runtime — never baked into the revision.
svc_secret_vars() {
  case "$1" in
    embedding) echo "" ;;
    pipeline)  echo "SUPABASE_KEY FINNHUB_API_KEY FMP_API_KEY" ;;
    question)  echo "SUPABASE_KEY ANTHROPIC_API_KEY" ;;
    backend)   echo "SUPABASE_KEY SUPABASE_JWT_SECRET ANTHROPIC_API_KEY" ;;
  esac
}
# Non-secret config, copied verbatim from the service's local .env.
svc_plain_vars() {
  case "$1" in
    embedding) echo "EMBEDDING_MODEL EMBEDDING_DIM" ;;
    pipeline)  echo "SUPABASE_URL EDGAR_USER_AGENT DEFAULT_TICKERS" ;;
    question)  echo "SUPABASE_URL LLM_MODEL_SUMMARY LLM_MODEL_QA LLM_MODEL_CLASSIFY LLM_EFFORT_QA" ;;
    backend)   echo "SUPABASE_URL LLM_MODEL LLM_EFFORT ALLOWED_ORIGINS ALLOWED_ORIGIN_REGEX" ;;
  esac
}
# Which upstream URLs this service needs injected. Resolved at deploy time from
# what is actually live, so a service can never point at a stale URL.
svc_wired_vars() {
  case "$1" in
    embedding) echo "" ;;
    pipeline)  echo "EMBEDDING_SERVICE_URL" ;;
    question)  echo "EMBEDDING_SERVICE_URL DATA_PIPELINE_URL" ;;
    backend)   echo "EMBEDDING_SERVICE_URL QUESTION_SERVICE_URL" ;;
  esac
}
# memory cpu min-instances max-instances concurrency timeout
#
# min-instances is 0 everywhere. embedding pays a cold start for it (it loads
# all-MiniLM-L6-v2 on boot); that is a deliberate trade — a warm instance costs
# ~$12/month whether or not anyone asks it a question. max-instances is the
# blast radius if the URL is ever scraped, so keep it small.
svc_sizing() {
  case "$1" in
    embedding) echo "2Gi 1 0 3 8 300" ;;
    pipeline)  echo "512Mi 1 0 2 20 900" ;;
    question)  echo "512Mi 1 0 5 40 600" ;;
    backend)   echo "512Mi 1 0 5 40 600" ;;
  esac
}
validate_svc() { svc_dir "$1" >/dev/null 2>&1 || die "unknown service: $1 (have: $ALL_CLOUD_SERVICES)"; }
resolve_svcs() {
  if [ "$#" -eq 0 ]; then echo "$CLOUD_SERVICES"
  else for s in "$@"; do validate_svc "$s"; done; echo "$@"; fi
}

# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH. $2"; }

# Read a value from a .env WITHOUT sourcing it (sourcing executes code).
# Same implementation as dev.sh — deliberately duplicated rather than shared,
# because these two scripts must stay independently runnable.
env_val() {
  local file="$1" key="$2" v
  [ -f "$file" ] || return 1
  v="$(sed -n "s/^[[:space:]]*${key}[[:space:]]*=//p" "$file" | head -1)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$(printf '%s' "$v" | tr -d '\r' | sed 's/[[:space:]]*$//')"
}
looks_placeholder() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    ""|your*|"<"*">"|*xxx*|changeme*|todo*|replace*|sk-x*) return 0 ;;
    *) return 1 ;;
  esac
}
svc_envfile() { echo "$(svc_dir "$1")/.env"; }

project_id() {
  if [ -n "${DEPLOY_PROJECT:-}" ]; then printf '%s' "$DEPLOY_PROJECT"; return 0; fi
  local p; p="$(gcloud config get-value project 2>/dev/null || true)"
  case "$p" in ""|"(unset)") return 1 ;; *) printf '%s' "$p" ;; esac
}
image_base() { echo "$REGION-docker.pkg.dev/$(project_id)/$AR_REPO"; }
sa_email()   { echo "$(svc_sa_id "$1")@$(project_id).iam.gserviceaccount.com"; }

# Image tag = the git SHA that produced it, so a Cloud Run revision is always
# traceable back to a commit. A dirty tree gets a -dirty suffix and a warning:
# you can still ship it, you just can never reproduce it.
svc_tag() {
  local sha
  sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
  if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then echo "$sha-dirty"; else echo "$sha"; fi
}

save_url() {
  mkdir -p "$STATE"
  touch "$URLS_FILE"
  local key="$1" val="$2" tmp="$URLS_FILE.tmp"
  grep -v "^${key}=" "$URLS_FILE" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$URLS_FILE"
}
# Ask Cloud Run, not the cache — the cache is only a fallback for offline use.
live_url() {
  local name url
  name="$(svc_run_name "$1")"
  url="$(gcloud run services describe "$name" --region "$REGION" \
          --format='value(status.url)' 2>/dev/null || true)"
  [ -n "$url" ] && { printf '%s' "$url"; return 0; }
  [ -f "$URLS_FILE" ] && env_val "$URLS_FILE" "$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')_URL" || true
}
url_var_for() {
  case "$1" in
    EMBEDDING_SERVICE_URL) echo "embedding" ;;
    DATA_PIPELINE_URL)     echo "pipeline" ;;
    QUESTION_SERVICE_URL)  echo "question" ;;
  esac
}

# `gcloud builds submit` uploads the whole directory. Without this it will
# happily upload venv/ (which dev.sh put there — hundreds of MB) and, for the
# backend, node_modules from a sibling. gcloud falls back to .gitignore only
# when no .gcloudignore exists, and .gitignore is not the same list.
ensure_gcloudignore() {
  local dir="$1"
  [ -f "$dir/.gcloudignore" ] && return 0
  cat > "$dir/.gcloudignore" <<'IGNORE'
.git/
.gitignore
venv/
.venv/
node_modules/
__pycache__/
*.pyc
.env
.env.*
!.env.example
output/
.devstack/
.deploy/
IGNORE
  info "wrote $(basename "$dir")/.gcloudignore (keeps venv/ out of the build context)"
}

# ─────────────────────────────────────────────────────────────
# preflight
# ─────────────────────────────────────────────────────────────
cmd_preflight() {
  local blocked=0

  head1 "TOOLCHAIN"
  if command -v gcloud >/dev/null 2>&1; then ok "$(gcloud version 2>/dev/null | head -1)"
  else bad "gcloud missing -> https://cloud.google.com/sdk/docs/install"; blocked=$((blocked+1)); fi
  if gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | grep -q .; then
    ok "authenticated as $(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1)"
  else bad "not authenticated -> gcloud auth login"; blocked=$((blocked+1)); fi

  head1 "TARGET"
  if project_id >/dev/null 2>&1; then
    ok "project  $(project_id)"
    ok "region   $REGION"
    ok "registry $(image_base)"
  else
    bad "no project set -> gcloud config set project <id>, or DEPLOY_PROJECT=<id>"
    blocked=$((blocked+1))
  fi

  head1 "SOURCE"
  local sha
  sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
  if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
    warn "$sha (uncommitted changes — images will be tagged -dirty)"
  else ok "$sha"; fi

  head1 "SERVICES"
  local s file miss k v url
  for s in $ALL_CLOUD_SERVICES; do
    file="$(svc_envfile "$s")"; miss=""
    printf '\n  %s%-10s%s %s\n' "$C_BOLD" "$s" "$C_RESET" "$(svc_label "$s")"
    for k in $(svc_secret_vars "$s") $(svc_plain_vars "$s"); do
      case "$k" in ALLOWED_ORIGIN_REGEX|DEFAULT_TICKERS|LLM_EFFORT|LLM_EFFORT_QA) continue ;; esac   # genuinely optional
      v="$(env_val "$file" "$k" 2>/dev/null || true)"
      looks_placeholder "$v" && miss="$miss $k"
    done
    if [ ! -f "$file" ]; then
      bad ".env missing -> ./dev.sh setup $s"; blocked=$((blocked+1))
    elif [ -n "$miss" ]; then
      bad "missing values:$(printf ' %s' $miss)"; blocked=$((blocked+1))
    else
      ok "all required values present in .env"
    fi
    url="$(live_url "$s" 2>/dev/null || true)"
    [ -n "$url" ] && ok "live: $url" || info "not deployed yet"
  done

  head1 "NEXT"
  if [ "$blocked" -gt 0 ]; then
    printf '  %s%s check(s) failed.%s Fix those first.\n' "$C_YEL" "$blocked" "$C_RESET"
    return 1
  fi
  info "./deploy.sh bootstrap     once per project"
  info "./deploy.sh secrets       whenever a credential changes"
  info "./deploy.sh deploy        every time you ship"
}

# ─────────────────────────────────────────────────────────────
# bootstrap — one time per project
# ─────────────────────────────────────────────────────────────
cmd_bootstrap() {
  need_cmd gcloud "Install the Google Cloud SDK."
  local proj; proj="$(project_id)" || die "no project set"

  head1 "APIS"
  gcloud services enable \
    run.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com secretmanager.googleapis.com \
    --project "$proj" --quiet
  ok "run, cloudbuild, artifactregistry, secretmanager enabled"

  head1 "ARTIFACT REGISTRY"
  if gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" --project "$proj" >/dev/null 2>&1; then
    ok "$AR_REPO already exists in $REGION"
  else
    gcloud artifacts repositories create "$AR_REPO" \
      --repository-format=docker --location="$REGION" \
      --description="QuantAgent service images" --project "$proj" --quiet
    ok "created $(image_base)"
  fi

  head1 "SERVICE ACCOUNTS"
  # One SA per service, each granted only the secrets it actually reads. The
  # default compute SA has project Editor — using it would give the embedding
  # service, which needs no credentials at all, write access to everything.
  local s sa
  for s in $ALL_CLOUD_SERVICES; do
    sa="$(sa_email "$s")"
    if gcloud iam service-accounts describe "$sa" --project "$proj" >/dev/null 2>&1; then
      ok "$(svc_sa_id "$s") exists"
    else
      gcloud iam service-accounts create "$(svc_sa_id "$s")" \
        --display-name="QuantAgent $s" --project "$proj" --quiet
      ok "created $(svc_sa_id "$s")"
    fi
  done
  local me; me="$(gcloud config get-value account 2>/dev/null || true)"
  if [ -n "$me" ]; then
    for s in $ALL_CLOUD_SERVICES; do
      gcloud iam service-accounts add-iam-policy-binding "$(sa_email "$s")" \
        --member="user:$me" --role=roles/iam.serviceAccountUser \
        --project "$proj" --quiet >/dev/null 2>&1 || true
    done
    ok "$me can deploy as these service accounts"
  fi
  info "run ./deploy.sh secrets next — it grants each SA access to its secrets"
}

# ─────────────────────────────────────────────────────────────
# secrets
# ─────────────────────────────────────────────────────────────
# Every secret's authoritative local value lives in the central secrets file
# that dev.sh manages (./dev.sh secrets). One secret in Secret Manager, many
# consumers — SUPABASE_KEY is one string, never three different secrets.
SECRETS_FILE="${QUANT_SECRETS_FILE:-$HOME/.config/quantagent/secrets.env}"
secret_source_file() { echo "$SECRETS_FILE"; }

cmd_secrets() {
  need_cmd gcloud "Install the Google Cloud SDK."
  local proj; proj="$(project_id)" || die "no project set"
  local svcs; svcs="$(resolve_svcs "$@")"

  head1 "SECRET MANAGER"
  local wanted="" s k
  for s in $svcs; do wanted="$wanted $(svc_secret_vars "$s")"; done
  wanted="$(printf '%s' "$wanted" | tr ' ' '\n' | sort -u | tr '\n' ' ')"

  for k in $wanted; do
    [ -z "$k" ] && continue
    local src val
    src="$(secret_source_file "$k")"
    val="$(env_val "$src" "$k" 2>/dev/null || true)"
    if looks_placeholder "$val"; then
      bad "$k: no real value in $src — skipped (./dev.sh secrets init)"
      continue
    fi
    if ! gcloud secrets describe "$k" --project "$proj" >/dev/null 2>&1; then
      gcloud secrets create "$k" --replication-policy=automatic --project "$proj" --quiet
      info "$k: created"
    fi
    # Only add a version when the value actually changed — Secret Manager keeps
    # every version forever and you pay per active version.
    local current
    current="$(gcloud secrets versions access latest --secret="$k" --project "$proj" 2>/dev/null || true)"
    if [ "$current" = "$val" ]; then
      ok "$k: unchanged"
    else
      printf '%s' "$val" | gcloud secrets versions add "$k" --data-file=- --project "$proj" --quiet >/dev/null
      ok "$k: new version added"
    fi
  done

  head1 "ACCESS"
  for s in $svcs; do
    local sa; sa="$(sa_email "$s")"
    for k in $(svc_secret_vars "$s"); do
      gcloud secrets add-iam-policy-binding "$k" \
        --member="serviceAccount:$sa" \
        --role=roles/secretmanager.secretAccessor \
        --project "$proj" --quiet >/dev/null 2>&1 || true
    done
    [ -n "$(svc_secret_vars "$s")" ] && ok "$s -> $(svc_secret_vars "$s")" || ok "$s needs no secrets"
  done
}

# ─────────────────────────────────────────────────────────────
# deploy
# ─────────────────────────────────────────────────────────────
build_image() {
  local svc="$1" dir img cfg
  dir="$(svc_context "$svc")"
  img="$(image_base)/$svc:$(svc_tag "$svc")"
  ensure_gcloudignore "$dir"
  mkdir -p "$STATE"
  cfg="$STATE/cloudbuild.$svc.yaml"
  # Explicit docker build rather than --source: apps/summarization holds two
  # images, and buildpack autodetection would pick the wrong entrypoint.
  cat > "$cfg" <<YAML
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', '$(svc_dockerfile "$svc")', '-t', '$img', '-t', '$(image_base)/$svc:latest', '.']
images:
  - '$img'
  - '$(image_base)/$svc:latest'
options:
  logging: CLOUD_LOGGING_ONLY
YAML
  info "$svc: building $img"
  ( cd "$dir" && gcloud builds submit --config "$cfg" --quiet . ) \
    || die "$svc: build failed — the Cloud Build log URL is printed above."
  # Returned via a global, not stdout: capturing stdout would swallow the build log.
  BUILT_IMAGE="$img"
}

deploy_one() {
  local svc="$1" img name sa sizing mem cpu mn mx conc tmo file
  name="$(svc_run_name "$svc")"; sa="$(sa_email "$svc")"; file="$(svc_envfile "$svc")"
  sizing="$(svc_sizing "$svc")"
  mem="$(echo "$sizing" | awk '{print $1}')"; cpu="$(echo "$sizing" | awk '{print $2}')"
  mn="$(echo "$sizing" | awk '{print $3}')";  mx="$(echo "$sizing" | awk '{print $4}')"
  conc="$(echo "$sizing" | awk '{print $5}')"; tmo="$(echo "$sizing" | awk '{print $6}')"

  build_image "$svc"; img="$BUILT_IMAGE"

  # ^@^ delimiter, not the default comma: ALLOWED_ORIGINS and DEFAULT_TICKERS
  # are comma-separated lists and would otherwise be parsed as extra vars.
  local envs="" k v target
  for k in $(svc_plain_vars "$svc"); do
    v="$(env_val "$file" "$k" 2>/dev/null || true)"
    [ -z "$v" ] && continue
    envs="$envs@$k=$v"
  done
  for k in $(svc_wired_vars "$svc"); do
    target="$(url_var_for "$k")"
    v="$(live_url "$target" 2>/dev/null || true)"
    if [ -z "$v" ]; then
      warn "$svc: $k unresolved ($target is not deployed) — leaving the service default"
      continue
    fi
    envs="$envs@$k=$v"
  done
  local secrets=""
  for k in $(svc_secret_vars "$svc"); do secrets="$secrets,$k=$k:latest"; done

  set -- "$name" \
    --image "$img" \
    --region "$REGION" \
    --platform managed \
    --service-account "$sa" \
    --memory "$mem" --cpu "$cpu" \
    --min-instances "$mn" --max-instances "$mx" \
    --concurrency "$conc" --timeout "$tmo" \
    --allow-unauthenticated \
    --quiet
  [ -n "$envs" ]    && set -- "$@" --set-env-vars "^@^${envs#@}"
  [ -n "$secrets" ] && set -- "$@" --set-secrets "${secrets#,}"

  gcloud run deploy "$@" >/dev/null || die "$svc: deploy failed"

  local url; url="$(live_url "$svc")"
  save_url "$(printf '%s' "$svc" | tr '[:lower:]' '[:upper:]')_URL" "$url"
  ok "$svc -> $url"

  if curl -fsS -m 30 "$url/health" >/dev/null 2>&1; then ok "$svc: /health OK"
  else warn "$svc: /health did not answer (cold start can take ~60s on embedding)"; fi
}

cmd_deploy() {
  need_cmd gcloud "Install the Google Cloud SDK."
  need_cmd git "Install git first."
  project_id >/dev/null 2>&1 || die "no project set"
  local svcs ordered="" s; svcs="$(resolve_svcs "$@")"
  # Dependency order: vectors -> live lookups -> RAG -> BFF. Each step resolves
  # the URLs of everything before it, so wiring is never hand-copied.
  for s in embedding pipeline question backend; do
    case " $svcs " in *" $s "*) ordered="$ordered $s" ;; esac
  done
  head1 "DEPLOY  ($(project_id) / $REGION)"
  for s in $ordered; do
    printf '\n  %s%s%s  %s\n' "$C_BOLD" "$s" "$C_RESET" "$(svc_label "$s")"
    deploy_one "$s"
  done
  head1 "LIVE"
  cmd_urls
}

# ─────────────────────────────────────────────────────────────
# inspect / recover
# ─────────────────────────────────────────────────────────────
cmd_urls() {
  local s url
  for s in $ALL_CLOUD_SERVICES; do
    url="$(live_url "$s" 2>/dev/null || true)"
    [ -n "$url" ] && ok "$(printf '%-10s %s' "$s" "$url")" || info "$(printf '%-10s not deployed' "$s")"
  done
}

cmd_status() {
  local s name
  for s in $ALL_CLOUD_SERVICES; do
    name="$(svc_run_name "$s")"
    gcloud run services describe "$name" --region "$REGION" >/dev/null 2>&1 || continue
    head1 "$s  ($name)"
    gcloud run revisions list --service "$name" --region "$REGION" \
      --format='table(REVISION,ACTIVE,SERVICE,DEPLOYED,DEPLOYED_BY)' --limit 5
  done
}

cmd_rollback() {
  [ "$#" -eq 1 ] || die "usage: ./deploy.sh rollback <service>"
  validate_svc "$1"
  local name prev
  name="$(svc_run_name "$1")"
  # Second-newest revision — the one that was serving before the last deploy.
  prev="$(gcloud run revisions list --service "$name" --region "$REGION" \
           --format='value(metadata.name)' --sort-by='~metadata.creationTimestamp' \
           --limit 2 | tail -1)"
  [ -n "$prev" ] || die "$1: no previous revision to roll back to"
  gcloud run services update-traffic "$name" --region "$REGION" --to-revisions "$prev=100" --quiet
  ok "$1: 100% traffic -> $prev"
}

cmd_logs() {
  [ "$#" -ge 1 ] || die "usage: ./deploy.sh logs <service>"
  validate_svc "$1"
  local name; name="$(svc_run_name "$1")"
  if gcloud beta run services logs tail "$name" --region "$REGION" 2>/dev/null; then return 0; fi
  # Older/other SDK channels have no `logs tail` — fall back to a plain read.
  info "no 'logs tail' in this SDK; showing the last 50 entries instead"
  gcloud logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=$name" \
    --limit 50 --format='value(timestamp,textPayload)' --freshness=1h
}

# ─────────────────────────────────────────────────────────────
# frontend (Vercel)
# ─────────────────────────────────────────────────────────────
cmd_cors() {
  [ "$#" -ge 1 ] || die "usage: ./deploy.sh cors https://your-app.vercel.app"
  local origins; origins="$(printf '%s' "$*" | tr ' ' ',')"
  gcloud run services update "$(svc_run_name backend)" --region "$REGION" \
    --update-env-vars "^@^ALLOWED_ORIGINS=$origins@ALLOWED_ORIGIN_REGEX=https://.*\.vercel\.app" \
    --quiet >/dev/null
  ok "backend CORS -> $origins (+ preview deploys via regex)"
  info "also mirror this into apps/backend/.env so the next full deploy keeps it"
}

cmd_frontend() {
  local burl; burl="$(live_url backend 2>/dev/null || true)"
  [ -n "$burl" ] || die "backend is not deployed yet — ./deploy.sh deploy backend first"
  head1 "VERCEL"
  say "  The frontend is a static Vite build; it is not deployed by this script."
  say ""
  say "    cd $ROOT/apps/web"
  say "    vercel env add VITE_API_URL production      # paste: $burl"
  say "    vercel --prod"
  say ""
  say "  Then point the backend back at whatever domain Vercel gave you:"
  say ""
  say "    ./deploy.sh cors https://<your-app>.vercel.app"
  say ""
  info "VITE_* vars are baked in at build time — changing one needs a rebuild, not a restart."
}

# ─────────────────────────────────────────────────────────────
usage() { awk 'NR>1 { if (/^#/) { sub(/^# ?/, ""); print } else exit }' "${BASH_SOURCE[0]}"; }

main() {
  local cmd="${1:-all}"; [ "$#" -gt 0 ] && shift || true
  case "$cmd" in
    all)       cmd_preflight || die "preflight failed — nothing was deployed."
               cmd_deploy ;;
    preflight) cmd_preflight ;;
    bootstrap) cmd_bootstrap ;;
    secrets)   cmd_secrets "$@" ;;
    deploy)    cmd_deploy "$@" ;;
    urls)      cmd_urls ;;
    status)    cmd_status ;;
    rollback)  cmd_rollback "$@" ;;
    logs)      cmd_logs "$@" ;;
    cors)      cmd_cors "$@" ;;
    frontend)  cmd_frontend ;;
    -h|--help|help) usage ;;
    *) die "unknown command: $cmd  (try ./deploy.sh --help)" ;;
  esac
}
main "$@"
