#!/usr/bin/env bash
# Start the Wildframe development stack from the current checkout.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT/deployments/docker-compose.dev.yml"

# Development TLS is generated locally and never committed.
bash "$ROOT/scripts/generate-dev-certs.sh"
docker compose -f "$COMPOSE_FILE" up --build -d
docker compose -f "$COMPOSE_FILE" ps

echo "Gateway: https://localhost:8000"
echo "Frontend: https://localhost:3000"