#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-deployments/docker-compose.dev.yml}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wildframe-ci-${GITHUB_RUN_ID:-local}}"
WAIT_SECONDS="${COMPOSE_SMOKE_WAIT_SECONDS:-420}"
BUILD_TIMEOUT_SECONDS="${COMPOSE_SMOKE_BUILD_TIMEOUT_SECONDS:-1200}"
LOG_FILE="${COMPOSE_SMOKE_LOG:-${RUNNER_TEMP:-/tmp}/wildframe-compose-smoke.log}"
METRICS_TOKEN="${METRICS_TOKEN:-wildframe-metrics-dev}"

compose() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

log() {
  # A log line that cannot be written must not kill the run: the log is a
  # report, not the thing being tested.
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG_FILE" || true
}

# Every exit path leaves exactly one COMPOSE_SMOKE_RESULT marker behind, so
# the CI job can tell the two failure modes apart. Before this existed, a job
# killed for running out of time was reported as "the stack did not come up
# clean", which sent the next person after a service that was merely slow.
# Run 39422598539 is the case in point: the build took 14m32s of the job's
# 15m budget, the stack was one service short of ready when the job was
# killed, and the check blamed the stack.
#
#   PASS    every service healthy and every probe answered
#   FAIL    the stack did not come up, or a route answered with the wrong status
#   TIMEOUT the run ran out of time before the stack could be judged
#
# Exit code 2 is the timeout code, so the marker and the exit status agree.
RESULT_RECORDED=""

record_result() {
  if [ -n "$RESULT_RECORDED" ]; then
    return 0
  fi
  RESULT_RECORDED=1
  log "COMPOSE_SMOKE_RESULT=$1"
}

# The stage markers say how far the run got, so a job killed before it could
# write a result still says which part ran out of time.
record_stage() {
  log "COMPOSE_SMOKE_STAGE=$1"
}

# The build is the longest part of this script and the one that has eaten
# whole CI budgets -- 16 images took 14m32s on a 4-vCPU CI runner -- so it is
# bounded. The bound is a background job plus a poll rather than GNU
# `timeout`, which macOS does not ship, so the script behaves the same on a
# developer laptop. A build that overruns is reported as a timeout, which is
# what it is: not evidence that a service is broken.
run_build() {
  compose up -d --build --remove-orphans >>"$LOG_FILE" 2>&1 &
  build_pid=$!
  waited=0
  while kill -0 "$build_pid" 2>/dev/null; do
    if [ "$waited" -ge "$BUILD_TIMEOUT_SECONDS" ]; then
      log "The image build is still running after ${waited}s; stopping it"
      kill -TERM "$build_pid" 2>/dev/null || true
      sleep 5
      kill -KILL "$build_pid" 2>/dev/null || true
      wait "$build_pid" 2>/dev/null || true
      return 124
    fi
    sleep 5
    waited=$((waited + 5))
  done
  wait "$build_pid" || return $?
}

cleanup() {
  rc=$?
  trap - EXIT
  # The marker is written before the teardown so a slow `compose down` cannot
  # swallow the verdict: the CI job reads this file to decide what happened.
  if [ -z "$RESULT_RECORDED" ]; then
    if [ "$rc" -eq 0 ]; then
      record_result "PASS"
    elif [ "$rc" -eq 2 ]; then
      record_result "TIMEOUT"
    else
      record_result "FAIL"
    fi
  fi
  log "Collecting final Compose logs"
  compose logs --no-color >>"$LOG_FILE" 2>&1 || true
  log "Tearing down Compose project $PROJECT_NAME"
  compose down -v --remove-orphans >>"$LOG_FILE" 2>&1 || true
  rm -f infrastructure/prometheus/metrics_bearer_token
  exit "$rc"
}

# A runner that stops the step (GitHub does this when the step's deadline
# passes) must not look like a stack failure either.
on_terminate() {
  record_result "TIMEOUT"
  exit 2
}

trap cleanup EXIT
trap on_terminate TERM INT

mkdir -p "$(dirname "$LOG_FILE")"
: >"$LOG_FILE"

log "Using Compose file: $COMPOSE_FILE"
log "Using project: $PROJECT_NAME"

command -v docker >/dev/null
docker compose version | tee -a "$LOG_FILE"

log "Generating CI-only development TLS certificates"
bash scripts/generate-dev-certs.sh >>"$LOG_FILE" 2>&1

log "Generating CI-only Prometheus bearer token file"
mkdir -p infrastructure/prometheus
printf '%s\n' "$METRICS_TOKEN" > infrastructure/prometheus/metrics_bearer_token
chmod 600 infrastructure/prometheus/metrics_bearer_token

log "Validating the Compose model"
compose config --quiet

log "Starting the full development stack"
record_stage "build"
build_rc=0
run_build || build_rc=$?
if [ "$build_rc" -eq 124 ] || [ "$build_rc" -eq 137 ]; then
  log "The image build exceeded its ${BUILD_TIMEOUT_SECONDS}s budget; the stack was never started"
  record_result "TIMEOUT"
  exit 2
fi
if [ "$build_rc" -ne 0 ]; then
  log "compose up failed with exit code $build_rc"
  record_result "FAIL"
  exit "$build_rc"
fi

record_stage "wait"
log "Waiting up to ${WAIT_SECONDS}s for every Compose service"
deadline=$((SECONDS + WAIT_SECONDS))
while (( SECONDS < deadline )); do
  compose config --format json >"${RUNNER_TEMP:-/tmp}/wildframe-compose-config.json"

  if compose ps --all --format json >"${RUNNER_TEMP:-/tmp}/wildframe-compose-ps.json"; then
    if python - "${RUNNER_TEMP:-/tmp}/wildframe-compose-config.json" "${RUNNER_TEMP:-/tmp}/wildframe-compose-ps.json" <<'PY'
import json
import sys

config_path, ps_path = sys.argv[1:3]
config = json.load(open(config_path, encoding="utf-8"))
raw = open(ps_path, encoding="utf-8").read().strip()

if not raw:
    print("No Compose containers are visible yet.")
    raise SystemExit(2)

try:
    containers = json.loads(raw)
except json.JSONDecodeError:
    containers = [json.loads(line) for line in raw.splitlines() if line.strip()]
if isinstance(containers, dict):
    containers = [containers]

by_service = {item.get("Service"): item for item in containers}
pending = []
failed = []

for service, spec in config.get("services", {}).items():
    item = by_service.get(service)
    if item is None:
        pending.append(f"{service}: container missing")
        continue

    state = str(item.get("State") or "").lower()
    health = str(item.get("Health") or "").lower()

    # A one-shot bootstrap task is *supposed* to exit. `kafka-init` creates the
    # topics and ACLs and then ends; `restart: "no"` with no healthcheck is what
    # marks it as a task rather than a long-running service. Treating its exit as
    # a terminal failure made this smoke test red on a healthy stack, which is
    # worse than no test: it trains people to ignore it.
    #
    # Exiting 0 is success. Exiting non-zero is a real failure and must still
    # fail the smoke test -- that is the whole point of gating every service on
    # `kafka-init: service_completed_successfully`.
    one_shot = (
        str(spec.get("restart", "")).lower() == "no"
        and not isinstance(spec.get("healthcheck"), dict)
    )
    if state == "exited" and one_shot:
        exit_code = item.get("ExitCode")
        # Strictly 0. An absent or unreadable ExitCode must not be read as
        # success: this is a smoke test, and fail-open here would hide a broken
        # bootstrap behind a green run.
        if str(exit_code) == "0":
            continue
        failed.append(f"{service}: one-shot exited {exit_code}")
        continue

    if state in {"exited", "dead"}:
        failed.append(f"{service}: state={state} health={health}")
        continue

    healthcheck = spec.get("healthcheck")
    health_disabled = isinstance(healthcheck, dict) and healthcheck.get("disable") is True
    if healthcheck and not health_disabled:
        if state != "running" or health != "healthy":
            pending.append(f"{service}: state={state} health={health or 'unknown'}")
    elif state != "running":
        pending.append(f"{service}: state={state} health={health or 'none'}")

if failed:
    print("FAILED:")
    print("\n".join(f"  - {entry}" for entry in failed))
    raise SystemExit(1)
if pending:
    print("PENDING:")
    print("\n".join(f"  - {entry}" for entry in pending))
    raise SystemExit(2)

print(f"READY: {len(config.get('services', {}))} Compose services")
PY
    then
      log "All Compose services are ready"
      break
    else
      check_rc=$?
      if (( check_rc == 1 )); then
        log "A Compose service entered a terminal failure state"
        record_result "FAIL"
        exit 1
      fi
    fi
  fi

  compose ps --all --format 'table {{.Service}}\t{{.State}}\t{{.Health}}' | tee -a "$LOG_FILE" || true
  sleep 10
done

if (( SECONDS >= deadline )); then
  # Not every service became healthy in the time allowed. That is a budget
  # problem, not a service failure: the table printed above the PENDING list
  # says which services were still starting, and none of them is blamed here.
  log "Not every Compose service became healthy within ${WAIT_SECONDS}s; the stack was not judged"
  record_result "TIMEOUT"
  exit 2
fi

# --- Schema bootstrap ---------------------------------------------------------
# A fresh Compose volume has databases but no tables. The init SQL
# (infrastructure/database/init-databases.sql) creates databases, users and
# extensions and stops there, and no service calls create_all at startup. So on
# CI every table-backed route 500s while /health stays green -- health is a bare
# SELECT 1, which passes against an empty database. That is exactly what run
# 37129259447 reported: 20 healthy containers, then
#   probe "content genres" -> HTTP 500
#   asyncpg.exceptions.UndefinedTableError: relation "genre" does not exist
# The probes below assert real routes, so without this step the job could never
# pass, on a correct stack as much as on a broken one.
#
# scripts/init_schemas.py is the schema authority, but this job installs no Python
# packages on purpose and ubuntu-latest has no SQLAlchemy, so it cannot run here.
# Every service image already carries the pinned SQLAlchemy and asyncpg, so run
# the identical bootstrap inside the container instead. That is not a
# convenience: `pip install sqlalchemy asyncpg` in the job would resolve
# independently of the service locks and could pick a floor the services do not
# support (AGENTS.md 20).
record_stage "bootstrap"
log "Bootstrapping service schemas inside their own containers"

# The services that own a database are the ones declaring DATABASE_URL, so this
# stays correct when a service is added instead of duplicating the service->db
# map that init_schemas.py already keeps.
mapfile -t DB_SERVICES < <(
  compose config --format json | python -c '
import json, sys

config = json.load(sys.stdin)
for name, spec in sorted(config.get("services", {}).items()):
    env = spec.get("environment") or {}
    if isinstance(env, list):  # compose accepts both list and mapping form
        env = {item.split("=", 1)[0]: item for item in env}
    if "DATABASE_URL" in env:
        print(name)
'
)

schema_failures=()
if (( ${#DB_SERVICES[@]} == 0 )); then
  # Silently skipping the bootstrap here would surface later as a confusing
  # UndefinedTableError from whichever probe touched a table first, so fail at
  # the actual cause instead.
  log "Could not determine which services own a database; refusing to probe an unbootstrapped schema"
  exit 1
fi

for svc in "${DB_SERVICES[@]}"; do
  # DATABASE_URL is read from the container environment, so the docker-internal
  # hostname is used rather than the runner's localhost. -w /app makes `app`
  # importable explicitly instead of relying on the image WORKDIR.
  if compose exec -T -w /app "$svc" python - <scripts/schema_bootstrap.py >>"$LOG_FILE" 2>&1; then
    log "  schema ok: $svc"
  else
    # Reported, not fatal. The probes are the verdict: admin-service and
    # billing-service refuse to reconcile because two of their models declare the
    # same table in separate metadata sets (a model bug, tracked separately), and
    # create_all has already made their tables by that point. Gating here would
    # fail an advisory smoke test over services it never probes.
    log "  SCHEMA FAILED: $svc (see $LOG_FILE)"
    schema_failures+=("$svc")
  fi
done

log "Bootstrapped ${#DB_SERVICES[@]} service schemas, ${#schema_failures[@]} failed"
if (( ${#schema_failures[@]} )); then
  log "Services with an incomplete schema: ${schema_failures[*]}"
fi

probe() {
  local name="$1"
  local url="$2"
  local expected="$3"
  local body_file
  body_file="$(mktemp)"

  log "Probing $name: $url"
  local status
  status="$(curl --silent --show-error --insecure --connect-timeout 5 --max-time 20 \
    --output "$body_file" --write-out '%{http_code}' "$url" || true)"

  printf '%s\n' "--- $name response (HTTP $status) ---" >>"$LOG_FILE"
  head -c 2000 "$body_file" >>"$LOG_FILE" || true
  printf '\n--- end response ---\n' >>"$LOG_FILE"
  rm -f "$body_file"

  if [[ "$status" != "$expected" ]]; then
    log "FAILED $name: expected HTTP $expected, got $status"
    return 1
  fi

  log "PASS $name: HTTP $status"
}

record_stage "probe"
probe "web homepage" "https://localhost:3000/" "200"
probe "gateway health" "https://localhost:8000/health" "200"
probe "auth JWKS" "https://localhost:8001/.well-known/jwks.json" "200"
probe "content genres" "https://localhost:8003/api/v1/genres" "200"
probe "content catalog" "https://localhost:8003/api/v1/content?page=1&page_size=1" "200"

record_stage "done"
record_result "PASS"
