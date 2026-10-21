"""Contract tests for the Compose Runtime Smoke CI budget (#1045).

The `Compose Runtime Smoke (advisory)` job used to carry `timeout-minutes: 15`
while the work it has to do -- `compose up -d --build` for 16 images, then a
420-second health wait -- needed more than that on its own. The job was killed
before it could judge anything, and the check then reported "the stack did not
come up clean", so a run that had run out of time was indistinguishable from a
run that had found a broken stack. Run 39422598539 is the evidence: the build
took 14m32s of the 15m budget and the stack was one service short of ready
when the job was killed.

Two properties are asserted here, and both are load-bearing:

* **The budget fits the work.** The job's deadline has to cover the measured
  image build, the health wait and the stages that follow them, and the step
  that runs the stack has to carry a deadline of its own so a run that
  overruns is stopped at the step instead of taking the job -- and the verdict
  that follows it -- down with it.
* **The verdict tells the two failures apart.** These are not text assertions:
  the verdict step's own shell block is executed here against a log file that
  says PASS, FAIL, TIMEOUT or nothing at all, and the summary and the exit
  status it produces are checked. A check that cannot distinguish "the stack
  did not come up" from "we ran out of time" *is* the defect.

The script side is exercised by running the real `scripts/compose-smoke.sh`
against a fake `docker`, the same way `tests/contract/conftest.py` fakes `ip`
and `ifconfig` for the certificate script: the behaviour under test is the
markers the script leaves behind, and faking the daemon is the only way to
reach them without one.

PyYAML plus the standard library, no docker and no network:

    python -m pytest tests/contract -q
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci-cd.yml"
SMOKE = REPO / "scripts" / "compose-smoke.sh"

#: `compose up -d --build --remove-orphans` for the 16 images took 14m32s on
#: the 4-vCPU CI runner in run 39422598539. The budget below is derived from
#: this number, so it is written down here rather than left in a comment: if
#: the measurement changes, the budget has to change with it.
MEASURED_BUILD_SECONDS = 872

#: `COMPOSE_SMOKE_BUILD_TIMEOUT_SECONDS` default in scripts/compose-smoke.sh.
BUILD_BOUND_SECONDS = 1200

#: `COMPOSE_SMOKE_WAIT_SECONDS` default in scripts/compose-smoke.sh.
HEALTH_WAIT_SECONDS = 420

#: Schema bootstrap, the five endpoint probes and the `compose down -v`
#: teardown, all of which run after the stack is up.
POST_STACK_SECONDS = 300

#: The smallest job budget that covers the work above.
REQUIRED_JOB_MINUTES = math.ceil(
    (BUILD_BOUND_SECONDS + HEALTH_WAIT_SECONDS + POST_STACK_SECONDS) / 60
)

#: The name of the log file the smoke script writes and the job reads.
LOG_NAME = "wildframe-compose-smoke.log"


# --- the workflow ------------------------------------------------------------


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _smoke_job() -> dict:
    job = _workflow()["jobs"]["compose-smoke"]
    assert job, "the workflow has no compose-smoke job"
    return job


def _step(job: dict, name: str) -> dict:
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r} in the compose-smoke job, "
        f"found {len(matches)}"
    )
    return matches[0]


def test_smoke_job_budget_covers_the_measured_build_and_the_health_wait():
    """The job's deadline has to be bigger than the work it has to do."""
    timeout = _smoke_job().get("timeout-minutes")
    assert isinstance(timeout, int), (
        "the compose-smoke job must declare an integer timeout-minutes; the "
        "default (360) hides the relationship between the budget and the work"
    )
    assert timeout >= REQUIRED_JOB_MINUTES, (
        f"the compose-smoke job has {timeout} minutes, but the work it must do "
        f"is {BUILD_BOUND_SECONDS}s of image build (measured "
        f"{MEASURED_BUILD_SECONDS}s on the CI runner in run 39422598539) + "
        f"{HEALTH_WAIT_SECONDS}s of health wait + {POST_STACK_SECONDS}s of "
        f"schema bootstrap, probes and teardown = {REQUIRED_JOB_MINUTES} minutes"
    )


def test_smoke_step_has_its_own_deadline_inside_the_job_budget():
    """The step deadline is what produces a verdict.

    A job-level deadline fires against the whole job, so the steps that
    report the verdict go down with it and a run that ran out of time leaves
    no verdict at all. A step-level deadline stops the stack on its own and
    leaves the `if: always()` steps below free to report.
    """
    job = _smoke_job()
    step = _step(job, "Run Compose runtime smoke")
    step_timeout = step.get("timeout-minutes")
    assert isinstance(step_timeout, int), (
        "the step that runs the stack must carry its own timeout-minutes"
    )
    assert step_timeout <= job["timeout-minutes"], (
        f"the step deadline ({step_timeout}m) is longer than the job deadline "
        f"({job['timeout-minutes']}m), so the job would be cancelled before "
        "the step could be stopped and the verdict would never be written"
    )
    assert step_timeout >= REQUIRED_JOB_MINUTES, (
        f"the step deadline is {step_timeout} minutes, which is less than the "
        f"{REQUIRED_JOB_MINUTES} minutes of work it has to do"
    )


def test_the_verdict_step_runs_whatever_happened_to_the_smoke_step():
    step = _step(_smoke_job(), "Report the Compose smoke verdict")
    assert step.get("if") == "always()", (
        "the verdict step must run even when the stack did not come up; "
        "otherwise the reader is left with a cancelled job and no explanation"
    )


# --- the verdict, executed ----------------------------------------------------


def _run_verdict(tmp_path: Path, log_text: str) -> tuple[subprocess.CompletedProcess, str]:
    """Run the verdict step's own shell block against a log file.

    The block is taken out of the workflow rather than reimplemented here, so
    the assertions cannot drift away from the code CI actually runs.
    """
    script = _step(_smoke_job(), "Report the Compose smoke verdict")["run"]

    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    (runner_temp / LOG_NAME).write_text(log_text, encoding="utf-8")
    summary = tmp_path / "summary.md"

    env = dict(os.environ)
    env["RUNNER_TEMP"] = str(runner_temp)
    env["GITHUB_STEP_SUMMARY"] = str(summary)

    proc = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    summary_text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return proc, summary_text


def test_verdict_reports_a_healthy_stack_as_a_pass(tmp_path):
    proc, summary = _run_verdict(
        tmp_path, "[2026-10-20T12:44:51Z] COMPOSE_SMOKE_RESULT=PASS\n"
    )
    assert proc.returncode == 0, summary
    assert "Compose Runtime Smoke: PASS" in summary


def test_verdict_reports_a_stack_failure_as_a_stack_failure(tmp_path):
    """A service that failed or a route that answered wrongly is a finding
    about the application."""
    proc, summary = _run_verdict(
        tmp_path, "[2026-10-20T12:44:51Z] COMPOSE_SMOKE_RESULT=FAIL\n"
    )
    assert proc.returncode == 1, summary
    assert "Compose Runtime Smoke: FAIL" in summary
    assert "did not come up" in summary
    assert "ran out of time" not in summary


def test_verdict_reports_a_timeout_as_a_timeout_not_as_a_stack_failure(tmp_path):
    """This is the case the issue is about: a run that ran out of time used to
    be reported as a stack that did not come up, which sent the next person
    after a service that was merely slow."""
    proc, summary = _run_verdict(
        tmp_path, "[2026-10-20T12:44:51Z] COMPOSE_SMOKE_RESULT=TIMEOUT\n"
    )
    assert proc.returncode == 2, summary
    assert "Compose Runtime Smoke: TIMEOUT" in summary
    assert "ran out of time" in summary
    assert "did not come up" not in summary


def test_verdict_reports_a_killed_run_as_a_timeout(tmp_path):
    """A run killed before it could write a result marker has no marker at
    all. That is still a timeout -- the deadline is what killed it -- and it
    must not be reported as a broken stack."""
    proc, summary = _run_verdict(
        tmp_path, "[2026-10-20T12:44:51Z] COMPOSE_SMOKE_STAGE=build\n"
    )
    assert proc.returncode == 2, summary
    assert "Compose Runtime Smoke: TIMEOUT" in summary
    # The stage that was reached says where the time went.
    assert "build" in summary
    assert "did not come up" not in summary


# --- the script, executed against a fake docker --------------------------------

FAKE_DOCKER = """#!/usr/bin/env bash
# A fake `docker` for the compose-smoke contract test. It answers the Compose
# subcommands the smoke script uses, so the script can be exercised without a
# Docker daemon. `FAKE_DOCKER_UP` decides what `compose up` does.
set -uo pipefail

case "${1:-}" in
  compose)
    shift
    while [ "${1:-}" = "-p" ] || [ "${1:-}" = "-f" ]; do
      shift 2
    done
    subcommand="${1:-}"
    shift || true
    case "$subcommand" in
      version)
        echo "Docker Compose version v2.29.0"
        exit 0
        ;;
      up)
        case "${FAKE_DOCKER_UP:-ok}" in
          hang) sleep 15; exit 0 ;;
          fail) exit "${FAKE_DOCKER_UP_RC:-1}" ;;
          *) exit 0 ;;
        esac
        ;;
      config)
        echo '{"services": {"fake-service": {"healthcheck": {"test": ["CMD", "true"]}}}}'
        exit 0
        ;;
      ps)
        # One service that never finishes starting: the wait loop keeps
        # waiting, which is the state a too-small budget produces.
        echo '{"Service": "fake-service", "State": "running", "Health": "starting"}'
        exit 0
        ;;
      *)
        exit 0
        ;;
    esac
    ;;
  *)
    exit 0
    ;;
esac
"""


def _run_smoke_script(
    tmp_path: Path, env_extra: dict[str, str]
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the real scripts/compose-smoke.sh against a fake `docker`.

    The script is copied into a sandbox because it resolves its certificates
    and its Prometheus token relative to its own location; running it in the
    checkout would overwrite a developer's certificates.
    """
    sandbox = tmp_path / "sandbox"
    (sandbox / "scripts").mkdir(parents=True)
    for name in ("compose-smoke.sh", "generate-dev-certs.sh", "schema_bootstrap.py"):
        shutil.copy2(REPO / "scripts" / name, sandbox / "scripts" / name)

    bindir = sandbox / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)

    runner_temp = sandbox / "runner"
    runner_temp.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["RUNNER_TEMP"] = str(runner_temp)
    env["COMPOSE_SMOKE_LOG"] = str(sandbox / "smoke.log")
    env.update(env_extra)

    proc = subprocess.run(
        ["bash", "scripts/compose-smoke.sh"],
        cwd=sandbox,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    log_path = sandbox / "smoke.log"
    log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return proc, log_text


def test_the_script_reports_a_build_that_overruns_as_a_timeout(tmp_path):
    """A build that eats the budget is a budget problem. Reporting it as a
    broken stack is what sent the next person after Elasticsearch."""
    proc, log_text = _run_smoke_script(
        tmp_path,
        {
            "COMPOSE_SMOKE_BUILD_TIMEOUT_SECONDS": "1",
            "COMPOSE_SMOKE_WAIT_SECONDS": "1",
            "FAKE_DOCKER_UP": "hang",
        },
    )
    assert proc.returncode == 2, log_text
    assert "COMPOSE_SMOKE_RESULT=TIMEOUT" in log_text
    assert "COMPOSE_SMOKE_RESULT=FAIL" not in log_text


def test_the_script_reports_a_failed_compose_up_as_a_stack_failure(tmp_path):
    proc, log_text = _run_smoke_script(
        tmp_path,
        {
            "FAKE_DOCKER_UP": "fail",
            "FAKE_DOCKER_UP_RC": "1",
        },
    )
    assert proc.returncode == 1, log_text
    assert "COMPOSE_SMOKE_RESULT=FAIL" in log_text
    assert "COMPOSE_SMOKE_RESULT=TIMEOUT" not in log_text


def test_the_script_reports_the_health_wait_deadline_as_a_timeout(tmp_path):
    """Not every service became healthy in the time allowed. That is a budget
    problem, not a service failure -- no service is named as the cause."""
    proc, log_text = _run_smoke_script(
        tmp_path,
        {
            "COMPOSE_SMOKE_WAIT_SECONDS": "1",
        },
    )
    assert proc.returncode == 2, log_text
    assert "COMPOSE_SMOKE_RESULT=TIMEOUT" in log_text
    assert "COMPOSE_SMOKE_RESULT=FAIL" not in log_text


# --- the shared vocabulary ----------------------------------------------------


def test_the_script_records_exactly_the_verdicts_the_job_understands():
    """The script and the job have to agree on the vocabulary, or a marker the
    script writes is a marker the job cannot read."""
    text = SMOKE.read_text(encoding="utf-8")
    recorded = set(re.findall(r'record_result "([A-Z]+)"', text))
    assert recorded == {"PASS", "FAIL", "TIMEOUT"}, (
        f"the script records {sorted(recorded)}; the job distinguishes PASS, "
        "FAIL and TIMEOUT"
    )


def test_the_build_bound_is_not_tighter_than_the_measured_build():
    """The bound exists to catch a pathological build, not to fire on a normal
    one. It was measured at 872s on the CI runner."""
    text = SMOKE.read_text(encoding="utf-8")
    match = re.search(r"COMPOSE_SMOKE_BUILD_TIMEOUT_SECONDS:-(\d+)", text)
    assert match, "the smoke script has no COMPOSE_SMOKE_BUILD_TIMEOUT_SECONDS default"
    bound = int(match.group(1))
    assert bound >= MEASURED_BUILD_SECONDS, (
        f"the image build is bounded at {bound}s, but it took "
        f"{MEASURED_BUILD_SECONDS}s on the CI runner, so the bound would fire "
        "on a normal run and report a timeout for a build that was on time"
    )
