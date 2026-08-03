#!/usr/bin/env bash
# Smoke test placeholder. Real implementation lives in services/<name>/tests/.
# This file exists so CI deploy steps that reference it don't 404.
set -e
ENV="${1:-staging}"
echo "[smoke] would run smoke tests against $ENV"
exit 0
