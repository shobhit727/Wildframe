#!/usr/bin/env bash
# Build and start the complete Wildframe development stack from any checkout path.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT/deployments/docker-compose.dev.yml"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running."
  exit 1
fi

# Generate local TLS material before services that mount it start.
bash "$ROOT/scripts/generate-dev-certs.sh"
docker compose -f "$COMPOSE_FILE" build
docker compose -f "$COMPOSE_FILE" up -d
docker compose -f "$COMPOSE_FILE" ps