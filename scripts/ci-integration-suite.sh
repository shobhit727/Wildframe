#!/usr/bin/env bash
# Run the cross-service integration suite (tests/integration) against a stack
# that is ALREADY UP, and turn its result into a verdict that cannot be green
# by accident.
#
# CI runs this from inside scripts/compose-smoke.sh (COMPOSE_SMOKE_POST_PASS=
# scripts/ci-integration-suite.sh), after the smoke probes pass and before the
# script's EXIT trap collects logs and tears the stack down (#1119). It also
# works by hand against a running dev stack:
#
#   bash scripts/ci-integration-suite.sh                  # run the whole suite
#   bash scripts/ci-integration-suite.sh -k gateway_auth  # extra args go to pytest
#   bash scripts/ci-integration-suite.sh --help
#
# Why not plain `pytest tests/integration`: the suite's conftest *skips* every
# test when the gateway is unreachable, and pytest exits 0 on an all-skipped
# run. This script fails fast on an unreachable gateway and reads the JUnit
# report afterwards, so "nothing really ran" is a failure, not a pass.
#
# Environment:
#   WILDFRAME_GATEWAY_URL       gateway base URL (default https://localhost:8000)
#   PYTHON                      interpreter with pytest + httpx (default python3)
#   INTEGRATION_JUNIT           JUnit report path
#                               (default ${RUNNER_TEMP:-<repo>/tem}/wildframe-integration-junit.xml)
#   INTEGRATION_TIMEOUT_SECONDS bound on the pytest run (default 1800); needs GNU timeout
#
# Exit codes:
#   0  every collected test passed or was skipped by its own data guard
#   1  a test failed or errored (names are printed), or pytest itself failed
#   2  usage error
#   3  the gateway health gate did not answer 200; pytest was not started
#   4  the suite did not really run: no report, zero tests, or every test skipped
#   5  the suite exceeded INTEGRATION_TIMEOUT_SECONDS
#
# The last line printed is INTEGRATION_SUITE_RESULT=<PASS|FAIL|UNREACHABLE|NOT_RUN|TIMEOUT>.
set -euo pipefail

usage() {
  sed -n '2,/^set -euo pipefail$/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'
}

case "${1:-}" in
  -h|--help)
    printf 'Usage: bash scripts/ci-integration-suite.sh [pytest args...]\n\n'
    usage
    exit 0
    ;;
  --*)
    # Long options are reserved for this script; pass pytest options after `--`.
    if [[ "$1" != "--" ]]; then
      printf 'Unknown option: %s (see --help)\n' "$1" >&2
      exit 2
    fi
    shift
    ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"

GATEWAY_URL="${WILDFRAME_GATEWAY_URL:-https://localhost:8000}"
PYTHON="${PYTHON:-python3}"
JUNIT="${INTEGRATION_JUNIT:-${RUNNER_TEMP:-$REPO_ROOT/tem}/wildframe-integration-junit.xml}"
SUITE_TIMEOUT="${INTEGRATION_TIMEOUT_SECONDS:-1800}"

result() {
  printf 'INTEGRATION_SUITE_RESULT=%s\n' "$1"
}

# The conftest gate (tests/integration/conftest.py:require_stack) probes this
# exact URL and skips the whole session when it is not 200. Check it here so a
# dead gateway is a loud failure instead of 110 quiet skips.
status="$(curl --silent --insecure --connect-timeout 5 --max-time 10 \
  --output /dev/null --write-out '%{http_code}' "${GATEWAY_URL}/gateway/health" || true)"
if [[ "$status" != "200" ]]; then
  printf '[integration] %s/gateway/health answered HTTP %s, not 200; not starting pytest\n' \
    "$GATEWAY_URL" "${status:-000}" >&2
  result UNREACHABLE
  exit 3
fi

mkdir -p "$(dirname "$JUNIT")"
rm -f "$JUNIT"

# -o addopts= drops the root pyproject's --cov flags (they target services/,
# not this suite); --strict-markers is kept from them on purpose. -v plus
# -rfEs puts a line per test and the reason for every failure, error and skip
# in the log; the JUnit file carries the same detail as an artifact.
pytest_cmd=("$PYTHON" -m pytest tests/integration -o addopts= --strict-markers
  -p no:cacheprovider -v -rfEs --tb=short --durations=15 --junitxml="$JUNIT" "$@")
if command -v timeout >/dev/null 2>&1; then
  pytest_cmd=(timeout --signal=TERM --kill-after=30 "$SUITE_TIMEOUT" "${pytest_cmd[@]}")
else
  printf '[integration] GNU timeout not found; the pytest run is unbounded\n' >&2
fi

export WILDFRAME_GATEWAY_URL="$GATEWAY_URL"
started=$SECONDS
pytest_rc=0
"${pytest_cmd[@]}" || pytest_rc=$?
printf '[integration] pytest exited %s after %ss\n' "$pytest_rc" "$((SECONDS - started))"

if (( pytest_rc == 124 || pytest_rc == 137 )); then
  printf '[integration] the suite exceeded its %ss budget\n' "$SUITE_TIMEOUT" >&2
  result TIMEOUT
  exit 5
fi

eval_rc=0
"$PYTHON" - "$JUNIT" <<'PY' || eval_rc=$?
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

report = Path(sys.argv[1])
if not report.is_file():
    print(f"[integration] no JUnit report at {report}: the suite did not run")
    raise SystemExit(4)

root = ET.parse(report).getroot()
cases = list(root.iter("testcase"))
failed, errored, skipped = [], [], []
for case in cases:
    name = f"{case.get('classname', '')}::{case.get('name', '')}"
    if case.find("failure") is not None:
        failed.append(name)
    elif case.find("error") is not None:
        errored.append(name)
    elif case.find("skipped") is not None:
        skipped.append((name, case.find("skipped").get("message", "")))

passed = len(cases) - len(failed) - len(errored) - len(skipped)
print(
    f"[integration] tests={len(cases)} passed={passed} failed={len(failed)} "
    f"errors={len(errored)} skipped={len(skipped)}"
)
for label, names in (("FAILED", failed), ("ERROR", errored)):
    for name in names:
        print(f"[integration] {label} {name}")

if failed or errored:
    raise SystemExit(1)
if not cases:
    print("[integration] zero tests collected: the suite did not run")
    raise SystemExit(4)
if not passed:
    reason = skipped[0][1] if skipped else ""
    print(f"[integration] every test was skipped, so nothing was verified: {reason}")
    raise SystemExit(4)
PY

if (( eval_rc == 0 && pytest_rc != 0 )); then
  # The report looks clean but pytest did not exit 0 (internal or usage error).
  printf '[integration] pytest exited %s with a clean report; treating it as a failure\n' "$pytest_rc" >&2
  eval_rc=1
fi

case "$eval_rc" in
  0) result PASS ;;
  4) result NOT_RUN ;;
  *) result FAIL; eval_rc=1 ;;
esac
exit "$eval_rc"
