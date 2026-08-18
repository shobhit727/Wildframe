#!/usr/bin/env bash
# Run the cross-service integration suite under tests/integration.
# Requires the Wildframe docker-compose stack to be up and reachable
# via $WILDFRAME_GATEWAY_URL (default https://localhost:8000).
# See tests/integration/conftest.py for the stack gate.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

GATEWAY_URL="${WILDFRAME_GATEWAY_URL:-https://localhost:8000}"
HOST="${GATEWAY_URL#*://}"
HOST="${HOST%%/*}"
PORT="${HOST##*:}"
HOST="${HOST%%:*}"
SCHEME="${GATEWAY_URL%%:*}"

# Fail fast if the gateway isn't reachable — don't let pytest silently skip.
if ! curl --silent --fail --insecure --output /dev/null \
    --max-time 5 "${SCHEME}://${HOST}:${PORT}/health"; then
    echo "[integration] gateway unreachable at ${GATEWAY_URL}" >&2
    echo "[integration] start the stack: deployments/docker-compose.dev.yml up -d" >&2
    exit 1
fi

exec pytest tests/integration --asyncio-mode=auto -q "$@"
