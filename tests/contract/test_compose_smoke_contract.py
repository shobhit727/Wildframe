"""Contract tests for the Compose runtime smoke test (#938).

`scripts/compose-smoke.sh` is the only check in this repo that stands the whole
stack up and drives real routes, so two properties of it matter more than the
rest of the script put together:

* **Its wait loop can tell "still starting" from "ready" from "broken".** The
  loop reads each service's healthcheck out of the Compose *config*, not out of
  `docker compose ps`, because 15 of the 29 services have no healthcheck at all
  and report `Health: ""` forever. A loop that required `healthy` from every
  container could therefore never succeed, and a loop that ignored terminal
  states could never fail. Both are checked here by *executing the script's own
  embedded python* against synthetic Compose output, so the assertions cannot
  drift away from the shipped code.
* **Its DB-backed probes are satisfiable.** A fresh Compose volume has databases
  and no tables, so `probe "content genres"` returned HTTP 500
  (`UndefinedTableError: relation "genre" does not exist`) on a stack whose
  containers were all healthy. The script must bootstrap schemas before probing.

Stdlib only, no docker and no network:

    python -m pytest tests/contract -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SMOKE = REPO / "scripts" / "compose-smoke.sh"
BOOTSTRAP = REPO / "scripts" / "schema_bootstrap.py"


def _wait_loop_source() -> str:
    """The python the wait loop actually runs, lifted out of the shell script.

    Read from the file rather than duplicated here, so a change to the loop is
    what gets tested. The heredoc is opened with ``<<'PY'`` and closed by a line
    containing only ``PY``.
    """
    lines = SMOKE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip().endswith("<<'PY'"))
    end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line == "PY")
    return "\n".join(lines[start + 1 : end])


def _run_wait_loop(services: dict, containers: list[dict]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.json"
        ps = Path(tmp) / "ps.json"
        cfg.write_text(json.dumps({"services": services}), encoding="utf-8")
        # `docker compose ps --format json` emits one JSON object per line.
        ps.write_text("\n".join(json.dumps(c) for c in containers), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-", str(cfg), str(ps)],
            input=_wait_loop_source(),
            capture_output=True,
            text=True,
        )
    return proc.returncode, proc.stdout


def _hc() -> dict:
    return {"test": ["CMD", "true"]}


# --- the loop can tell starting from ready from broken -----------------------


def test_all_healthchecked_healthy_and_healthcheckless_running_is_ready():
    """The healthy case: 2 healthchecked healthy, 2 infra containers with no
    healthcheck at all. The infra pair report Health:"" and must not hold the
    loop open."""
    rc, out = _run_wait_loop(
        services={
            "content-service": {"healthcheck": _hc()},
            "auth-service": {"healthcheck": _hc()},
            "grafana": {},
            "caddy": {},
        },
        containers=[
            {"Service": "content-service", "State": "running", "Health": "healthy"},
            {"Service": "auth-service", "State": "running", "Health": "healthy"},
            {"Service": "grafana", "State": "running", "Health": ""},
            {"Service": "caddy", "State": "running", "Health": ""},
        ],
    )
    assert rc == 0, out
    assert "READY" in out


def test_healthcheckless_containers_are_not_pending_forever():
    """Regression guard for the loop logic.

    Every service here has no healthcheck, so every container reports Health:"".
    If the loop demanded `healthy` from each one it would spin to its deadline
    and time out on a perfectly good stack -- which is the failure mode this
    test exists to prevent.
    """
    rc, out = _run_wait_loop(
        services={"grafana": {}, "caddy": {}, "jaeger": {}, "loki": {}},
        containers=[
            {"Service": name, "State": "running", "Health": ""}
            for name in ("grafana", "caddy", "jaeger", "loki")
        ],
    )
    assert rc == 0, f"healthcheck-less containers were treated as pending: {out}"


def test_healthcheck_disabled_counts_as_no_healthcheck():
    rc, out = _run_wait_loop(
        services={"media-pipeline": {"healthcheck": {"disable": True}}},
        containers=[{"Service": "media-pipeline", "State": "running", "Health": ""}],
    )
    assert rc == 0, out


def test_starting_healthchecked_service_is_pending_not_failed():
    rc, out = _run_wait_loop(
        services={"auth-service": {"healthcheck": _hc()}},
        containers=[{"Service": "auth-service", "State": "running", "Health": "starting"}],
    )
    assert rc == 2, out
    assert "PENDING" in out
    assert "auth-service" in out


def test_running_but_unhealthy_is_pending_not_ready():
    rc, out = _run_wait_loop(
        services={"auth-service": {"healthcheck": _hc()}},
        containers=[{"Service": "auth-service", "State": "running", "Health": "unhealthy"}],
    )
    assert rc == 2, out


def test_exited_service_fails_the_loop():
    rc, out = _run_wait_loop(
        services={"auth-service": {"healthcheck": _hc()}},
        containers=[{"Service": "auth-service", "State": "exited", "Health": ""}],
    )
    assert rc == 1, out
    assert "FAILED" in out


def test_exited_healthcheckless_service_also_fails():
    """A container without a healthcheck is not exempt from being dead."""
    rc, out = _run_wait_loop(
        services={"grafana": {}},
        containers=[{"Service": "grafana", "State": "exited", "Health": ""}],
    )
    assert rc == 1, out


def test_missing_container_is_pending():
    rc, out = _run_wait_loop(
        services={"auth-service": {"healthcheck": _hc()}},
        containers=[],
    )
    assert rc == 2, out


def test_no_containers_visible_yet_is_pending():
    rc, out = _run_wait_loop(services={"auth-service": {"healthcheck": _hc()}}, containers=[])
    assert rc == 2, out


# --- the probes are satisfiable ---------------------------------------------


def test_smoke_script_bootstraps_schemas_before_probing():
    """Ordering is the whole fix: an unbootstrapped stack cannot pass a probe
    that reads a table, so the bootstrap must appear before the first probe."""
    text = SMOKE.read_text(encoding="utf-8")
    bootstrap_at = text.find("schema_bootstrap.py")
    assert bootstrap_at != -1, "smoke script never bootstraps schemas"
    first_probe = min(text.find(f'probe "{name}"') for name in ("web homepage", "gateway health"))
    assert first_probe != -1
    assert bootstrap_at < first_probe, "schemas are bootstrapped after the probes"


def test_smoke_script_bootstrap_reads_the_shared_implementation():
    """One implementation, so init_schemas.py and the smoke test cannot drift
    into creating different schemas."""
    assert BOOTSTRAP.is_file(), "scripts/schema_bootstrap.py is missing"
    assert "schema_bootstrap.py" in SMOKE.read_text(encoding="utf-8")
    # init_schemas.py must read the same file rather than embedding a second copy.
    init = (REPO / "scripts" / "init_schemas.py").read_text(encoding="utf-8")
    assert "schema_bootstrap.py" in init
    assert "RUNNER = r'''" not in init, (
        "init_schemas.py embeds its own copy of the bootstrap again; the single "
        "shared file exists so these two callers cannot diverge"
    )


def test_smoke_script_does_not_silence_the_bootstrap():
    """`|| true` on the bootstrap would hide the very failure it exists to
    report, so the failure has to stay observable in the exit status."""
    text = SMOKE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "schema_bootstrap.py" in line:
            assert "|| true" not in line, f"bootstrap failure is silenced: {line!r}"
            assert "continue-on-error" not in line, f"bootstrap failure is silenced: {line!r}"


def test_wait_budget_is_not_the_fix():
    """The stack reported every service ready 11s into a 420s budget, so the
    timeout was not the defect. If this number moves, it needs the evidence that
    justified it recorded here rather than being raised to mask a failure."""
    text = SMOKE.read_text(encoding="utf-8")
    assert "COMPOSE_SMOKE_WAIT_SECONDS:-420" in text, (
        "the wait budget changed; the observed time-to-ready was 11s of 420s, so "
        "a different value needs its own justification"
    )
