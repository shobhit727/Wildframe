#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-deployments/docker-compose.dev.yml}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wildframe-ci-${GITHUB_RUN_ID:-local}}"
WAIT_SECONDS="${COMPOSE_SMOKE_WAIT_SECONDS:-420}"
LOG_FILE="${COMPOSE_SMOKE_LOG:-${RUNNER_TEMP:-/tmp}/wildframe-compose-smoke.log}"
METRICS_TOKEN="${METRICS_TOKEN:-wildframe-metrics-dev}"

mkdir -p "$(dirname "$LOG_FILE")"
: >"$LOG_FILE"

compose() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG_FILE"
}

cleanup() {
  rc=$?
  trap - EXIT
  log "Collecting final Compose logs"
  compose logs --no-color >>"$LOG_FILE" 2>&1 || true
  log "Tearing down Compose project $PROJECT_NAME"
  compose down -v --remove-orphans >>"$LOG_FILE" 2>&1 || true
  rm -f infrastructure/prometheus/metrics_bearer_token
  exit "$rc"
}
trap cleanup EXIT

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
compose up -d --build --remove-orphans >>"$LOG_FILE" 2>&1

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
        exit 1
      fi
    fi
  fi

  compose ps --all --format 'table {{.Service}}\t{{.State}}\t{{.Health}}' | tee -a "$LOG_FILE" || true
  sleep 10
done

if (( SECONDS >= deadline )); then
  log "Timed out waiting for the Compose stack"
  exit 1
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

probe "web homepage" "https://localhost:3000/" "200"
probe "gateway health" "https://localhost:8000/health" "200"
probe "auth JWKS" "https://localhost:8001/.well-known/jwks.json" "200"
probe "content genres" "https://localhost:8003/api/v1/genres" "200"
probe "content catalog" "https://localhost:8003/api/v1/content?page=1&page_size=1" "200"

log "COMPOSE_SMOKE_RESULT=PASS"
