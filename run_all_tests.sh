#!/usr/bin/env bash
# Run every backend service and shared SDK test suite.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for service_dir in "$ROOT"/services/*-service; do
  [[ -d "$service_dir/tests" ]] || continue
  service="$(basename "$service_dir")"
  echo "==> Testing $service"
  (cd "$service_dir" && python -m pytest tests --asyncio-mode=auto)
done

(cd "$ROOT" && PYTHONPATH="$ROOT/packages/sdk${PYTHONPATH:+:$PYTHONPATH}" python -m pytest packages/sdk --asyncio-mode=auto -q)
echo "All backend and SDK tests completed successfully."