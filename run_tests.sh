#!/usr/bin/env bash
# Run every backend service test suite from the canonical tests/ directories.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
failed=0

for service_dir in "$ROOT"/services/*-service; do
  [[ -d "$service_dir/tests" ]] || continue
  service="$(basename "$service_dir")"
  echo "==> $service"
  if ! (cd "$service_dir" && python -m pytest tests --asyncio-mode=auto -q); then
    failed=1
  fi
done

echo "Service test sweep complete."
exit "$failed"