#!/usr/bin/env bash
#
# dev.sh — single entry point for the whole stack, in one monorepo.
#
#   git clone https://github.com/ZeroNoise2026/QuantAgent.git zeroNoise
#   cd zeroNoise && ./dev.sh
#
# Commands
#   ./dev.sh                 setup + doctor  (start here; idempotent)
#   ./dev.sh sync            git pull --ff-only
#   ./dev.sh setup [svc...]  venvs, deps, .env scaffolding
#   ./dev.sh doctor          what is ready, what is blocked, and why
#   ./dev.sh test            offline suites (zero credentials)
#   ./dev.sh up [svc...]     start services whose credentials are present
#   ./dev.sh down [svc...]   stop them
#   ./dev.sh status          which services are up
#   ./dev.sh logs <svc>      tail a service log
#   ./dev.sh env             which keys in .env are set (values masked)
#   ./dev.sh env pull        fill .env's secrets from GCP Secret Manager
#
# services: embedding question backend frontend
#
# Layout
#
#   apps/backend        FastAPI BFF                       (service: backend)
#   apps/web            React frontend                    (service: frontend)
#   apps/summarization  question service + summary CLI    (service: question)
#   apps/pipeline       batch ingestion (a job, not a service)
#   apps/embedding      embedding API                     (service: embedding)
#   libs/common         Claude client, Supabase client, audit log
#   libs/research       ticker context, report generation + cache
#   libs/skills         skill framework: registry, LLM router, built-in skills
#
# Rule: apps never import each other. Code two apps need goes in libs/.
#
# Env
#   ONE file for every service: <repo>/.env (gitignored), scaffolded from
#   .env.example by `setup`. Python apps load it from their config.py; the
#   frontend through Vite's envDir.
#   DEPLOY_PROJECT       GCP project for `env pull` (default: gcloud config)
#
# Written for bash 3.2 (what macOS still ships) — no associative arrays.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$ROOT/.devstack"
LOGS="$STATE/logs"
PIDS="$STATE/pids"

ALL_SERVICES="embedding question backend frontend"

ENV_FILE="$ROOT/.env"
# Secrets mirrored in GCP Secret Manager (see deploy.sh svc_secret_vars).
GSM_SECRETS="ANTHROPIC_API_KEY SUPABASE_KEY SUPABASE_JWT_SECRET FINNHUB_API_KEY FMP_API_KEY"

# ─────────────────────────────────────────────────────────────
# output
# ─────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
else
  C_RESET=""; C_DIM=""; C_BOLD=""; C_RED=""; C_GRN=""; C_YEL=""
fi
say()  { printf '%s\n' "$*"; }
head1(){ printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }
ok()   { printf '  %s✓%s %s\n' "$C_GRN" "$C_RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_YEL" "$C_RESET" "$*"; }
bad()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; }
info() { printf '  %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }
die()  { printf '\n%serror:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────
# service registry (case statements — bash 3.2 has no assoc arrays)
# ─────────────────────────────────────────────────────────────
svc_path() {
  case "$1" in
    embedding) echo "$ROOT/apps/embedding" ;;
    question)  echo "$ROOT/apps/summarization" ;;
    backend)   echo "$ROOT/apps/backend" ;;
    frontend)  echo "$ROOT/apps/web" ;;
    *) return 1 ;;
  esac
}
svc_port() {
  case "$1" in
    embedding) echo 8002 ;; question) echo 8003 ;;
    backend) echo 8000 ;; frontend) echo 3000 ;; *) return 1 ;;
  esac
}
svc_label() {
  case "$1" in
    embedding) echo "embedding-service (vectors)" ;;
    question)  echo "question-service (RAG Q&A)" ;;
    backend)   echo "backend (BFF)" ;;
    frontend)  echo "frontend (React)" ;;
  esac
}
# Env vars that must hold a real value before the service can usefully start.
# embedding needs NOTHING external — it is the one service always runnable.
svc_required() {
  case "$1" in
    embedding) echo "" ;;
    question)  echo "SUPABASE_URL SUPABASE_KEY ANTHROPIC_API_KEY" ;;
    backend)   echo "SUPABASE_URL SUPABASE_KEY SUPABASE_JWT_SECRET ANTHROPIC_API_KEY" ;;
    frontend)  echo "VITE_SUPABASE_URL VITE_SUPABASE_PUBLISHABLE_KEY" ;;
  esac
}
svc_start_cmd() {
  case "$1" in
    embedding) echo "venv/bin/uvicorn app.main:app --port 8002" ;;
    question)  echo "venv/bin/uvicorn question.main:app --port 8003" ;;
    backend)   echo "venv/bin/uvicorn main:app --port 8000" ;;
    frontend)  echo "npm run dev" ;;
  esac
}
# libs/ packages each Python service imports (installed editable by setup).
svc_libs() {
  case "$1" in
    backend)  echo "common research skills" ;;
    question) echo "common research skills marketdata" ;;
    *)        echo "" ;;
  esac
}
is_node_svc() { [ "$1" = "frontend" ]; }

# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
# Read a value from a .env WITHOUT sourcing it (sourcing executes code).
env_val() {
  local file="$1" key="$2" v
  [ -f "$file" ] || return 1
  v="$(sed -n "s/^[[:space:]]*${key}[[:space:]]*=//p" "$file" | head -1)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$(printf '%s' "$v" | tr -d '\r' | sed 's/[[:space:]]*$//')"
}

# The .env.example files mix real defaults (EMBEDDING_DIM=384) with
# placeholders (SUPABASE_KEY=your-supabase-service-role-key). Blanking
# everything breaks real config — config.py does
# int(os.getenv("EMBEDDING_DIM","384")), and a present-but-empty var is NOT
# the default, it's int(""). Copying verbatim would make doctor report
# placeholders as configured. So we tell the two apart.
looks_placeholder() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    ""|your*|*"<"*">"*|*xxx*|changeme*|todo*|replace*|sk-x*) return 0 ;;
    *) return 1 ;;
  esac
}

missing_vars() {
  local svc="$1" out="" k v
  for k in $(svc_required "$svc"); do
    v="$(env_val "$ENV_FILE" "$k" 2>/dev/null || true)"
    looks_placeholder "$v" && out="$out $k"
  done
  printf '%s' "${out# }"
}

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
pidfile()   { echo "$PIDS/$1.pid"; }
running()   { local f; f="$(pidfile "$1")"; [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; }

# Replace KEY=... in an existing .env. awk with ENVIRON, not sed, so values
# containing / & | or quotes are written verbatim.
set_env_val() {
  local file="$1" key="$2" val="$3" tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/devenv.XXXXXX")"
  KEY="$key" VAL="$val" awk '
    BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VAL"] }
    $0 ~ "^[[:space:]]*" k "[[:space:]]*=" { print k "=" v; next }
    { print }' "$file" > "$tmp"
  cat "$tmp" > "$file"; rm -f "$tmp"
}
has_env_key() { [ -f "$1" ] && grep -q "^[[:space:]]*$2[[:space:]]*=" "$1"; }
mask() { local v="$1"; [ "${#v}" -le 8 ] && echo "****" || echo "${v:0:4}…${v: -4}"; }
validate_svc() { svc_path "$1" >/dev/null 2>&1 || die "unknown service: $1 (have: $ALL_SERVICES)"; }
resolve_svcs() { if [ "$#" -eq 0 ]; then echo "$ALL_SERVICES"; else for s in "$@"; do validate_svc "$s"; done; echo "$@"; fi; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH. $2"; }

# ─────────────────────────────────────────────────────────────
# sync
# ─────────────────────────────────────────────────────────────
cmd_sync() {
  need_cmd git "Install git first."
  head1 "SYNC"
  # --ff-only: never invent a merge commit in a working checkout.
  if git -C "$ROOT" pull --ff-only --quiet; then ok "up to date ($(git -C "$ROOT" rev-parse --short HEAD))"
  else warn "fast-forward pull failed — local commits or a dirty tree? left untouched"; fi
}

# ─────────────────────────────────────────────────────────────
# setup
# ─────────────────────────────────────────────────────────────
scaffold_env() {
  local example="$ROOT/.env.example" line k v blanked=0
  if [ -f "$ENV_FILE" ]; then info ".env already exists — left untouched"; return 0; fi
  ( umask 077; : > "$ENV_FILE" )
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      [A-Z]*=*)
        k="${line%%=*}"; v="${line#*=}"
        if looks_placeholder "$v"; then printf '%s=\n' "$k" >> "$ENV_FILE"; blanked=$((blanked + 1))
        else printf '%s\n' "$line" >> "$ENV_FILE"; fi ;;
      *) printf '%s\n' "$line" >> "$ENV_FILE" ;;
    esac
  done < "$example"
  ok "scaffolded .env — kept real defaults, emptied $blanked placeholder(s); fill them in"
}

setup_python_svc() {
  local svc="$1" dir venv
  dir="$(svc_path "$svc")"; venv="$dir/venv"
  [ -d "$dir" ] || { warn "$svc: $dir missing"; return 0; }
  if [ ! -x "$venv/bin/python" ]; then info "$svc: creating venv"; python3 -m venv "$venv"; fi
  "$venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null
  if [ -f "$dir/requirements.txt" ]; then
    info "$svc: installing requirements (can take a few minutes)"
    "$venv/bin/python" -m pip install --quiet -r "$dir/requirements.txt"
  fi
  # libs/ packages install editable, so edits take effect without a reinstall.
  local libs; libs="$(svc_libs "$svc")"
  if [ -n "$libs" ]; then
    local lib args=""
    for lib in $libs; do args="$args -e $ROOT/libs/$lib"; done
    # shellcheck disable=SC2086
    "$venv/bin/python" -m pip install --quiet $args
    info "$svc: libs installed editable ($libs)"
  fi
  ok "$svc: python deps ready"
}

setup_frontend() {
  local dir; dir="$(svc_path frontend)"
  [ -d "$dir" ] || { warn "frontend: directory missing"; return 0; }
  need_cmd npm "Install Node 18+ from nodejs.org or via brew."
  if [ ! -d "$dir/node_modules" ]; then info "frontend: npm install"; ( cd "$dir" && npm install --silent ); fi
  ok "frontend: node deps ready"
}

cmd_setup() {
  need_cmd python3 "Install Python 3.10+."
  mkdir -p "$LOGS" "$PIDS"
  local svcs s; svcs="$(resolve_svcs "$@")"
  head1 "SETUP"
  scaffold_env
  for s in $svcs; do
    if is_node_svc "$s"; then setup_frontend; else setup_python_svc "$s"; fi
  done
  # libs/ is not a service, but its offline suites are the fastest proof the
  # codebase is intact — give it one shared venv. Non-fatal.
  local lv="$ROOT/libs/venv"
  if [ ! -x "$lv/bin/python" ]; then
    if python3 -m venv "$lv" \
       && "$lv/bin/python" -m pip install --quiet --upgrade pip >/dev/null \
       && "$lv/bin/python" -m pip install --quiet -e "$ROOT/libs/common" -e "$ROOT/libs/research" -e "$ROOT/libs/skills" -e "$ROOT/libs/marketdata"; then
      ok "libs: venv ready (editable install)"
    else
      warn "libs: editable install failed — ./dev.sh test will skip the libs suites"
    fi
  fi
}

# ─────────────────────────────────────────────────────────────
# doctor
# ─────────────────────────────────────────────────────────────
cmd_doctor() {
  head1 "WORKSPACE"
  info "$ROOT"
  ok "$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null)"

  env_status

  head1 "TOOLCHAIN"
  command -v python3 >/dev/null 2>&1 && ok "python3 $(python3 -V 2>&1 | awk '{print $2}')" || bad "python3 missing"
  command -v node >/dev/null 2>&1 && ok "node $(node -v)" || warn "node missing (frontend only)"

  head1 "SERVICES"
  local blocked=0 s dir venv miss
  for s in $ALL_SERVICES; do
    dir="$(svc_path "$s")"; venv="$dir/venv"; miss="$(missing_vars "$s")"
    printf '\n  %s%-10s%s %s  %s:%s%s\n' "$C_BOLD" "$s" "$C_RESET" "$(svc_label "$s")" "$C_DIM" "$(svc_port "$s")" "$C_RESET"
    if [ ! -d "$dir" ]; then bad "repo not present -> ./dev.sh sync"; blocked=$((blocked+1)); continue; fi
    if is_node_svc "$s"; then
      [ -d "$dir/node_modules" ] && ok "deps installed" || bad "node_modules missing -> ./dev.sh setup frontend"
    else
      [ -x "$venv/bin/python" ] && ok "venv present" || bad "no venv -> ./dev.sh setup $s"
    fi
    if [ -z "$(svc_required "$s")" ]; then ok "needs no external credentials"
    elif [ -z "$miss" ]; then ok "all required env vars set"
    else bad "missing values:$(printf ' %s' $miss)"; blocked=$((blocked+1)); fi
    running "$s" && ok "RUNNING (pid $(cat "$(pidfile "$s")"))" || true
  done

  head1 "WHAT YOU CAN DO RIGHT NOW"
  info "./dev.sh test    offline suites, zero credentials"
  if [ "$blocked" -gt 0 ]; then
    printf '\n  %s%s service(s) blocked.%s Supabase keys: Dashboard -> Project Settings -> API.\n' "$C_YEL" "$blocked" "$C_RESET"
  fi
}

# ─────────────────────────────────────────────────────────────
# test
# ─────────────────────────────────────────────────────────────
cmd_test() {
  head1 "OFFLINE SUITES (no credentials)"
  local rc=0
  if [ -x "$ROOT/libs/venv/bin/python" ]; then
    say "  skills smoke suite:"
    ( cd "$ROOT/libs/skills" && "$ROOT/libs/venv/bin/python" -m skills.tests.test_smoke ) || rc=1
  else
    warn "libs venv missing -> ./dev.sh setup"; rc=1
  fi
  if [ -x "$ROOT/apps/summarization/venv/bin/python" ]; then
    say ""; say "  summarization tests:"
    ( cd "$ROOT/apps/summarization" && ./venv/bin/python -m pytest -q tests ) || rc=1
  fi
  say ""; say "  module boundaries:"
  python3 "$ROOT/scripts/check_boundaries.py" || rc=1
  return $rc
}

# ─────────────────────────────────────────────────────────────
# env
# ─────────────────────────────────────────────────────────────
# Every key some service requires, de-duplicated, in a stable order.
required_keys() {
  local s; for s in $ALL_SERVICES; do svc_required "$s"; echo; done | tr ' ' '\n' | awk 'NF && !seen[$0]++'
}

env_status() {
  head1 "ENV  $ENV_FILE"
  [ -f "$ENV_FILE" ] || { warn "missing — ./dev.sh setup scaffolds it from .env.example"; return 0; }
  local k v
  for k in $(required_keys); do
    v="$(env_val "$ENV_FILE" "$k" 2>/dev/null || true)"
    if looks_placeholder "$v"; then bad "$k: empty"; else ok "$(printf '%-30s %s' "$k" "$(mask "$v")")"; fi
  done
}

# Fill .env's secrets from GCP Secret Manager (latest versions).
env_pull() {
  need_cmd gcloud "Install the Google Cloud SDK, then: gcloud auth login"
  local proj k v
  proj="${DEPLOY_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
  case "$proj" in ""|"(unset)") die "no GCP project: set DEPLOY_PROJECT or run gcloud config set project <id>" ;; esac
  [ -f "$ENV_FILE" ] || scaffold_env
  for k in $GSM_SECRETS; do
    if v="$(gcloud secrets versions access latest --secret="$k" --project "$proj" 2>/dev/null)"; then
      if has_env_key "$ENV_FILE" "$k"; then set_env_val "$ENV_FILE" "$k" "$v"
      else printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"; fi
      ok "$k: pulled"
    else
      warn "$k: not in Secret Manager ($proj) or no access — left as is"
    fi
  done
}

cmd_env() {
  case "${1:-status}" in
    status) env_status ;;
    pull)   head1 "PULL"; env_pull ;;
    *)      die "unknown: env $1 (status|pull)" ;;
  esac
}

# ─────────────────────────────────────────────────────────────
# up / down / status / logs
# ─────────────────────────────────────────────────────────────
start_svc() {
  local svc="$1" dir port miss log
  dir="$(svc_path "$svc")"; port="$(svc_port "$svc")"; log="$LOGS/$svc.log"
  running "$svc" && { info "$svc already running"; return 0; }
  [ -d "$dir" ] || { bad "$svc: repo missing -> ./dev.sh sync"; return 1; }
  if port_busy "$port"; then warn "$svc: port $port already in use — not starting"; return 0; fi
  if is_node_svc "$svc"; then
    [ -d "$dir/node_modules" ] || { bad "$svc: no node_modules -> ./dev.sh setup frontend"; return 1; }
  else
    [ -x "$dir/venv/bin/python" ] || { bad "$svc: no venv -> ./dev.sh setup $svc"; return 1; }
  fi
  miss="$(missing_vars "$svc")"
  if [ -n "$miss" ]; then
    # Refuse rather than start-and-crash: a stack trace 40 lines deep teaches
    # you nothing you don't already know right here.
    bad "$svc: not started — missing env:$(printf ' %s' $miss)"; return 1
  fi
  mkdir -p "$LOGS" "$PIDS"; : > "$log"
  # Statements are separated deliberately: `cd && cmd & echo $!` parses as
  # `{ cd && cmd } &`, which backgrounds the whole subshell (so piping dev.sh
  # hangs) and records dev.sh's own pid. `exec` makes the recorded pid the
  # real server, so `down` kills the right process.
  (
    cd "$dir" || exit 1
    nohup sh -c "exec $(svc_start_cmd "$svc")" >"$log" 2>&1 &
    printf '%s\n' "$!" > "$(pidfile "$svc")"
  ) </dev/null
  ok "$svc starting on :$port  (log: .devstack/logs/$svc.log)"
}

probe_url() {
  if command -v curl >/dev/null 2>&1; then curl -fsS -m 2 "$1" >/dev/null 2>&1
  else local hp="${1#http://}"; hp="${hp%%/*}"; ( exec 3<>"/dev/tcp/${hp%%:*}/${hp##*:}" ) >/dev/null 2>&1; fi
}

wait_healthy() {
  local svc="$1" port url tries=0 max=60
  port="$(svc_port "$svc")"
  # 127.0.0.1 not localhost: localhost can resolve to ::1 first and miss an
  # IPv4-only listener.
  if is_node_svc "$svc"; then url="http://127.0.0.1:$port"; else url="http://127.0.0.1:$port/health"; fi
  [ "$svc" = "embedding" ] && max=180   # first boot downloads all-MiniLM-L6-v2 (~90MB)
  while [ "$tries" -lt "$max" ]; do
    probe_url "$url" && { ok "$svc healthy ($url)"; return 0; }
    running "$svc" || { bad "$svc died on startup — tail .devstack/logs/$svc.log"; return 1; }
    tries=$((tries+1)); sleep 1
  done
  warn "$svc did not answer $url within ${max}s (check the log)"; return 1
}

cmd_up() {
  local svcs ordered="" started="" s; svcs="$(resolve_svcs "$@")"
  head1 "STARTING"
  # Dependency order: vectors -> RAG -> BFF -> UI.
  for s in embedding question backend frontend; do
    case " $svcs " in *" $s "*) ordered="$ordered $s" ;; esac
  done
  for s in $ordered; do start_svc "$s" && started="$started $s" || true; done
  [ -n "$started" ] || { warn "nothing started — run ./dev.sh doctor"; return 1; }
  head1 "HEALTH"; for s in $started; do wait_healthy "$s" || true; done
  head1 "URLS";   for s in $started; do info "$(printf '%-10s http://localhost:%s' "$s" "$(svc_port "$s")")"; done
  info "stop with: ./dev.sh down"
}

cmd_down() {
  local svcs s p; svcs="$(resolve_svcs "$@")"
  head1 "STOPPING"
  for s in $svcs; do
    if running "$s"; then
      p="$(cat "$(pidfile "$s")")"
      # `npm run dev` forks vite; kill children first or the port stays bound.
      pkill -TERM -P "$p" 2>/dev/null || true
      kill -TERM "$p" 2>/dev/null || true
      sleep 1
      if kill -0 "$p" 2>/dev/null; then pkill -KILL -P "$p" 2>/dev/null || true; kill -KILL "$p" 2>/dev/null || true; fi
      rm -f "$(pidfile "$s")"; ok "$s stopped"
    else
      rm -f "$(pidfile "$s")" 2>/dev/null || true; info "$s not running"
    fi
  done
}

cmd_status() {
  head1 "STATUS"
  local s
  for s in $ALL_SERVICES; do
    if running "$s"; then ok "$(printf '%-10s up   pid %s  :%s' "$s" "$(cat "$(pidfile "$s")")" "$(svc_port "$s")")"
    elif port_busy "$(svc_port "$s")"; then warn "$(printf '%-10s port %s busy (not started by dev.sh)' "$s" "$(svc_port "$s")")"
    else info "$(printf '%-10s down' "$s")"; fi
  done
}

cmd_logs() {
  [ "$#" -ge 1 ] || die "usage: ./dev.sh logs <service>"
  validate_svc "$1"
  [ -f "$LOGS/$1.log" ] || die "no log yet for $1"
  tail -f "$LOGS/$1.log"
}

usage() { sed -n '2,42p' "$0" | sed 's/^#\{1,2\} \{0,1\}//;s/^#$//'; }

main() {
  local cmd="${1:-default}"; [ "$#" -gt 0 ] && shift || true
  case "$cmd" in
    default)        cmd_setup; cmd_doctor ;;
    sync|pull)      cmd_sync ;;
    setup)          cmd_setup "$@" ;;
    doctor|check)   cmd_doctor ;;
    test)           cmd_test ;;
    up|start)       cmd_up "$@" ;;
    down|stop)      cmd_down "$@" ;;
    status|ps)      cmd_status ;;
    logs)           cmd_logs "$@" ;;
    env)            cmd_env "$@" ;;
    -h|--help|help) usage ;;
    *)              die "unknown command '$cmd' (try ./dev.sh --help)" ;;
  esac
}
main "$@"
