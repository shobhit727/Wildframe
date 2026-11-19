"""Contract tests: CI runs the live-stack integration suite (#1119).

`tests/integration/` (~110 tests: real JWTs, real databases, real Kafka) used to
run only when someone started the 29-container stack by hand. The only CI job
that stands the stack up is Compose Runtime Smoke, and `scripts/compose-smoke.sh`
tears the stack down from its EXIT trap, so a workflow step *after* the script
would find nothing to talk to. The suite therefore runs from a post-pass hook
*inside* the script, after the probes and before the PASS marker, while the
trap still guarantees log collection and teardown.

What is pinned here:

* when the suite runs: on pull requests the compose-smoke job runs the smoke
  only, on its old 15-minute budget, without the suite's dependencies; on
  pushes to main/develop and workflow_dispatch it runs smoke + suite on the
  65/60-minute budget; a separate nightly workflow runs smoke + suite on a
  schedule, so ci-cd.yml itself is never scheduled. The workflows' `${{ }}`
  expressions are *evaluated* per event here, not string-matched;
* the hook is wired to `scripts/ci-integration-suite.sh`, which runs
  `pytest tests/integration`, with nothing that turns a failure into a pass;
* the hook is off by default, refuses anything that is not a regular file
  directly under `scripts/`, and a failing hook fails the smoke run (no PASS
  marker) -- all asserted by *executing* the script's own hook function;
* the suite runner's report evaluation tells "passed" from "failed" from "did
  not really run" (the conftest gate skips the whole session when the gateway
  is unreachable, which must never read as green) -- asserted by executing the
  runner's own embedded python against synthetic JUnit reports.

Stdlib + PyYAML only, no docker and no network:

    python -m pytest tests/contract -q
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci-cd.yml"
NIGHTLY = REPO / ".github" / "workflows" / "integration-nightly.yml"
SMOKE = REPO / "scripts" / "compose-smoke.sh"
RUNNER = REPO / "scripts" / "ci-integration-suite.sh"
HOOK_VALUE = "scripts/ci-integration-suite.sh"
SUITE_EVENTS = ("push", "workflow_dispatch")


# --- evaluating the workflows' expressions per event -------------------------

_EXPR = re.compile(r"\$\{\{(.*)\}\}", re.S)
_ALLOWED_TOKENS = {"and", "or", "==", "!=", "(", ")"}


def _eval_expr(expr: str, event: str):
    """Evaluate the small subset of GitHub expression syntax these workflows
    use: `github.event_name`, string/int literals, ==, !=, &&, ||. GitHub's
    && / || return an operand (not a bool), exactly like Python's and / or."""
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = expr.replace("github.event_name", repr(event))
    leftover = re.sub(r"'[^']*'|\b\d+\b", " ", expr)
    leftover = leftover.replace("(", " ( ").replace(")", " ) ")
    unknown = set(leftover.split()) - _ALLOWED_TOKENS
    assert not unknown, f"expression uses syntax this test does not model: {unknown}"
    return eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - vetted tokens only


def _value(raw, event: str):
    """A workflow value as GitHub would see it for `event`: a whole-value
    `${{ }}` is evaluated, anything else is literal."""
    if isinstance(raw, str):
        match = _EXPR.fullmatch(raw.strip())
        if match:
            return _eval_expr(match.group(1), event)
    return raw


def _runs(step: dict, event: str) -> bool:
    condition = step.get("if")
    if condition is None:
        return True
    match = _EXPR.fullmatch(str(condition).strip())
    return bool(_eval_expr(match.group(1) if match else str(condition), event))


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # PyYAML (YAML 1.1) reads the bare key `on` as the boolean True.
    return doc.get("on", doc.get(True)) or {}


def _smoke_step(job: dict) -> int:
    for index, step in enumerate(job.get("steps", [])):
        if "scripts/compose-smoke.sh" in str(step.get("run", "")):
            return index
    raise AssertionError("no step runs scripts/compose-smoke.sh")


def _smoke_job() -> tuple[str, dict, int]:
    """The ci-cd.yml job and index of the step that runs compose-smoke.sh."""
    for name, job in _load(WORKFLOW)["jobs"].items():
        if any("scripts/compose-smoke.sh" in str(s.get("run", "")) for s in job.get("steps", [])):
            return name, job, _smoke_step(job)
    raise AssertionError("no CI step runs scripts/compose-smoke.sh")


def _installs_before(job: dict, index: int, event: str) -> str:
    return " ".join(str(s.get("run", "")) for s in job["steps"][:index] if _runs(s, event))


# --- ci-cd.yml: which events run the suite -----------------------------------


def test_ci_cd_triggers_and_is_never_scheduled():
    """Scheduling ci-cd.yml would run all of its jobs nightly; the nightly suite
    has its own workflow instead."""
    triggers = _triggers(_load(WORKFLOW))
    assert (
        "schedule" not in triggers
    ), "ci-cd.yml must not be scheduled; use integration-nightly.yml"
    for event in ("pull_request", *SUITE_EVENTS):
        assert event in triggers, f"ci-cd.yml no longer runs on {event}"


def test_pull_requests_run_the_smoke_only_on_the_old_budget():
    """Owner decision: the suite's ~17-18 min of paced waiting is not spent on
    every PR. A PR runs the smoke alone, as before #1119."""
    _, job, index = _smoke_job()
    step = job["steps"][index]
    hook = _value((step.get("env") or {}).get("COMPOSE_SMOKE_POST_PASS", ""), "pull_request")
    assert not hook, f"pull requests run the integration suite (hook={hook!r})"
    assert "pytest" not in _installs_before(
        job, index, "pull_request"
    ), "pull requests install the suite's dependencies they never use"
    assert _value(job.get("timeout-minutes"), "pull_request") == 15
    step_minutes = _value(step.get("timeout-minutes"), "pull_request")
    assert isinstance(step_minutes, int) and 0 < step_minutes < 15


def test_push_and_dispatch_run_the_integration_suite_against_the_composed_stack():
    """The acceptance bar of #1119: outside PRs, the step that has the stack up
    hands it to `pytest tests/integration`."""
    _, job, index = _smoke_job()
    step = job["steps"][index]
    for event in SUITE_EVENTS:
        hook = _value((step.get("env") or {}).get("COMPOSE_SMOKE_POST_PASS", ""), event)
        assert hook == HOOK_VALUE, f"{event} does not run the integration suite (hook={hook!r})"
        installs = _installs_before(job, index, event)
        for package in ("pytest", "httpx"):
            assert package in installs, f"{event}: {package} is not installed before the suite runs"
        job_minutes = _value(job.get("timeout-minutes"), event)
        step_minutes = _value(step.get("timeout-minutes"), event)
        assert (job_minutes, step_minutes) == (
            65,
            60,
        ), f"{event}: budget {job_minutes}/{step_minutes} does not fit build + wait + suite"
    runner = RUNNER.read_text(encoding="utf-8")
    assert "-m pytest tests/integration" in runner, "the hook does not run tests/integration"


def test_no_step_in_the_job_swallows_a_failure():
    """The job itself is advisory (#979, owner's call); no *step* may add a
    second layer of failure-hiding on top of that."""
    _, job, _ = _smoke_job()
    for step in job["steps"]:
        assert "continue-on-error" not in step, f"step {step.get('name')!r} hides failures"
        assert "|| true" not in str(
            step.get("run", "")
        ), f"step {step.get('name')!r} swallows a failure with || true"


def test_verdict_step_separates_a_suite_failure_from_a_smoke_failure():
    _, job, _ = _smoke_job()
    runs = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert (
        "COMPOSE_SMOKE_POST_PASS_RESULT=FAIL" in runs
    ), "the verdict step cannot tell 'stack did not come up' from 'integration suite failed'"


def test_junit_report_is_uploaded_for_per_test_detail():
    _, job, _ = _smoke_job()
    uploads = [s for s in job["steps"] if "upload-artifact" in str(s.get("uses", ""))]
    assert uploads, "no artifact upload in the compose-smoke job"
    paths = " ".join(str((s.get("with") or {}).get("path", "")) for s in uploads)
    assert "wildframe-integration-junit.xml" in paths


# --- integration-nightly.yml ---------------------------------------------------


def _uses(doc: dict) -> set[str]:
    return {
        str(step["uses"])
        for job in doc["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    }


def test_nightly_workflow_runs_the_suite_on_a_schedule_only():
    assert NIGHTLY.is_file(), "no nightly integration workflow"
    doc = _load(NIGHTLY)
    triggers = _triggers(doc)
    assert set(triggers) == {
        "schedule",
        "workflow_dispatch",
    }, f"nightly workflow triggers are {sorted(triggers)}; it must not run on PRs or pushes"
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert len(crons) == 1 and len(crons[0].split()) == 5, crons
    assert doc.get("permissions") == {"contents": "read"}
    assert doc.get("concurrency"), "nightly runs need a concurrency group"

    (job,) = doc["jobs"].values()
    index = _smoke_step(job)
    step = job["steps"][index]
    for event in ("schedule", "workflow_dispatch"):
        hook = _value((step.get("env") or {}).get("COMPOSE_SMOKE_POST_PASS", ""), event)
        assert hook == HOOK_VALUE, f"nightly {event} run does not run the suite"
        installs = _installs_before(job, index, event)
        assert "pytest" in installs and "httpx" in installs
    job_minutes, step_minutes = job.get("timeout-minutes"), step.get("timeout-minutes")
    assert isinstance(job_minutes, int) and isinstance(step_minutes, int)
    assert 0 < step_minutes < job_minutes

    assert "continue-on-error" not in job
    for s in job["steps"]:
        assert "continue-on-error" not in s, f"step {s.get('name')!r} hides failures"
        assert "|| true" not in str(s.get("run", "")), f"step {s.get('name')!r} swallows a failure"
    paths = " ".join(
        str((s.get("with") or {}).get("path", ""))
        for s in job["steps"]
        if "upload-artifact" in str(s.get("uses", ""))
    )
    assert "wildframe-integration-junit.xml" in paths


def test_nightly_workflow_reuses_the_action_pins_from_ci_cd():
    """Supply Chain Guard (#809) requires SHA pins; reusing ci-cd.yml's pins
    means no new third-party code enters through this workflow."""
    nightly = _uses(_load(NIGHTLY))
    assert nightly, "nightly workflow uses no actions?"
    for ref in nightly:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"{ref} is not SHA-pinned"
    unknown = nightly - _uses(_load(WORKFLOW))
    assert not unknown, f"nightly workflow introduces pins not used in ci-cd.yml: {unknown}"


# --- the hook inside compose-smoke.sh -----------------------------------------


def _hook_function_source() -> str:
    """`run_post_pass_hook` as shipped, lifted out of compose-smoke.sh."""
    lines = SMOKE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("run_post_pass_hook() {"))
    end = next(i for i, line in enumerate(lines[start:], start) if line == "}")
    return "\n".join(lines[start : end + 1])


def _run_hook(root: Path, value: str | None) -> tuple[int, str, str]:
    """Run the shipped hook under the script's own shell options, then the line
    that stands in for the PASS marker. Returns (rc, log file, output).

    The harness is written into ``<root>/scripts/`` and run from ``<root>``, the
    way CI runs ``bash scripts/compose-smoke.sh`` from the checkout, because the
    hook resolves the scripts directory from its own file location.
    """
    log_file = root / "smoke.log"
    harness = root / "scripts" / "smoke-harness.sh"
    harness.write_text(
        "\n".join(
            [
                "set -Eeuo pipefail",
                f"LOG_FILE={str(log_file)!r}",
                'log() { printf \'%s\\n\' "$*" | tee -a "$LOG_FILE"; }',
                _hook_function_source(),
                "run_post_pass_hook",
                'log "COMPOSE_SMOKE_RESULT=PASS"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_SMOKE_POST_PASS"}
    if value is not None:
        env["COMPOSE_SMOKE_POST_PASS"] = value
    proc = subprocess.run(
        ["bash", "scripts/smoke-harness.sh"], capture_output=True, text=True, env=env, cwd=root
    )
    text = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    return proc.returncode, text, proc.stdout + proc.stderr


def _fake_repo(tmp: str, hook_body: str = "exit 0") -> Path:
    root = Path(tmp).resolve()
    (root / "scripts").mkdir()
    hook = root / "scripts" / "hook.sh"
    hook.write_text(f"echo hook-ran\n{hook_body}\n", encoding="utf-8")
    return root


def test_hook_is_off_by_default_and_changes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        root = _fake_repo(tmp)
        rc, log, out = _run_hook(root, None)
    assert rc == 0, out
    assert log == "COMPOSE_SMOKE_RESULT=PASS\n", f"unset hook left traces: {log!r}"
    assert "hook-ran" not in out


def test_empty_hook_value_is_off():
    with tempfile.TemporaryDirectory() as tmp:
        rc, log, out = _run_hook(_fake_repo(tmp), "")
    assert rc == 0, out
    assert log == "COMPOSE_SMOKE_RESULT=PASS\n"


def test_passing_hook_runs_before_the_pass_marker():
    with tempfile.TemporaryDirectory() as tmp:
        rc, log, out = _run_hook(_fake_repo(tmp), "scripts/hook.sh")
    assert rc == 0, out
    assert "hook-ran" in log, "hook output is not in the smoke log artifact"
    assert log.index("COMPOSE_SMOKE_POST_PASS_RESULT=PASS") < log.index("COMPOSE_SMOKE_RESULT=PASS")


def test_failing_hook_fails_the_run_and_withholds_the_pass_marker():
    with tempfile.TemporaryDirectory() as tmp:
        rc, log, out = _run_hook(_fake_repo(tmp, "exit 3"), "scripts/hook.sh")
    assert rc != 0, "a failing hook was swallowed"
    assert "hook-ran" in log
    assert "COMPOSE_SMOKE_POST_PASS_RESULT=FAIL" in log
    assert "COMPOSE_SMOKE_RESULT=PASS" not in log


def _assert_refused(root: Path, value: str) -> None:
    rc, log, out = _run_hook(root, value)
    assert rc != 0, f"{value!r} was accepted"
    assert "hook-ran" not in out and "outside-ran" not in out, f"{value!r} was executed"
    assert "COMPOSE_SMOKE_POST_PASS_RESULT=FAIL" in log
    assert "COMPOSE_SMOKE_RESULT=PASS" not in log


def test_hook_refuses_paths_outside_scripts():
    with tempfile.TemporaryDirectory() as tmp:
        root = _fake_repo(tmp)
        outside = root / "outside.sh"
        outside.write_text("echo outside-ran\n", encoding="utf-8")
        (root / "scripts" / "nested").mkdir()
        (root / "scripts" / "nested" / "deep.sh").write_text("echo outside-ran\n", encoding="utf-8")
        for value in (
            "outside.sh",
            "scripts/../outside.sh",
            str(outside),
            "/bin/true",
            "scripts/nested/deep.sh",
            "scripts/missing.sh",
            "scripts",
            "scripts/",
        ):
            _assert_refused(root, value)


def test_hook_refuses_a_symlink_out_of_scripts():
    with tempfile.TemporaryDirectory() as tmp:
        root = _fake_repo(tmp)
        outside = root / "outside.sh"
        outside.write_text("echo outside-ran\n", encoding="utf-8")
        (root / "scripts" / "link.sh").symlink_to(outside)
        _assert_refused(root, "scripts/link.sh")


def test_hook_call_sits_after_the_probes_and_before_the_pass_marker():
    text = SMOKE.read_text(encoding="utf-8")
    lines = text.splitlines()
    call = [
        i
        for i, line in enumerate(lines)
        if line.strip().startswith("run_post_pass_hook")
        and not line.strip().startswith("run_post_pass_hook()")
    ]
    assert len(call) == 1, f"expected exactly one hook call, found {len(call)}"
    assert lines[call[0]].strip() == "run_post_pass_hook", "the hook call is decorated (|| true?)"
    last_probe = max(i for i, line in enumerate(lines) if line.startswith('probe "'))
    marker = next(
        i
        for i, line in enumerate(lines)
        if "COMPOSE_SMOKE_RESULT=PASS" in line and not line.lstrip().startswith("#")
    )
    assert last_probe < call[0] < marker


def test_teardown_trap_is_still_installed_before_anything_runs():
    text = SMOKE.read_text(encoding="utf-8")
    assert "trap cleanup EXIT" in text
    assert "compose down -v --remove-orphans" in text
    assert text.index("trap cleanup EXIT") < text.index("run_post_pass_hook\n")


# --- the suite runner ----------------------------------------------------------


def _evaluator_source() -> str:
    lines = RUNNER.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "<<'PY'" in line)
    end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line == "PY")
    return "\n".join(lines[start + 1 : end])


def _evaluate(junit_xml: str | None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        if junit_xml is not None:
            report.write_text(textwrap.dedent(junit_xml), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-", str(report)],
            input=_evaluator_source(),
            capture_output=True,
            text=True,
        )
    return proc.returncode, proc.stdout + proc.stderr


def _case(name: str, inner: str = "") -> str:
    return f'<testcase classname="tests.integration.m" name="{name}" time="0.1">{inner}</testcase>'


def _suite(*cases: str) -> str:
    return (
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    )


GATE_SKIP = (
    '<skipped type="pytest.skip" message="Wildframe docker stack not reachable at '
    'https://localhost:8000 (ConnectError())">x</skipped>'
)


def test_evaluator_passes_a_clean_run_and_reports_counts():
    rc, out = _evaluate(
        _suite(_case("a"), _case("b"), _case("c", '<skipped message="catalog is empty"/>'))
    )
    assert rc == 0, out
    assert "tests=3 passed=2 failed=0 errors=0 skipped=1" in out


def test_evaluator_fails_on_a_failed_test_and_names_it():
    rc, out = _evaluate(
        _suite(_case("a"), _case("test_broken", '<failure message="assert 500 == 200"/>'))
    )
    assert rc == 1, out
    assert "test_broken" in out


def test_evaluator_fails_on_an_error():
    rc, out = _evaluate(
        _suite(_case("a"), _case("test_setup", '<error message="fixture blew up"/>'))
    )
    assert rc == 1, out


def test_stack_gate_skip_is_not_green():
    """conftest.require_stack *skips* every test when the gateway is down. A run
    where every test skipped that way exits 0 from pytest; it must not here."""
    rc, out = _evaluate(_suite(_case("a", GATE_SKIP), _case("b", GATE_SKIP)))
    assert rc == 4, out


def test_zero_tests_is_not_green():
    rc, out = _evaluate(_suite())
    assert rc == 4, out


def test_all_skipped_is_not_green():
    rc, out = _evaluate(_suite(_case("a", '<skipped message="catalog is empty"/>')))
    assert rc == 4, out


def test_missing_report_is_not_green():
    rc, out = _evaluate(None)
    assert rc == 4, out


def test_runner_help_and_usage_exit_codes():
    ok = subprocess.run(["bash", str(RUNNER), "--help"], capture_output=True, text=True)
    assert ok.returncode == 0 and "Usage" in ok.stdout, ok.stdout + ok.stderr
    bad = subprocess.run(["bash", str(RUNNER), "--no-such-flag"], capture_output=True, text=True)
    assert bad.returncode == 2, bad.stdout + bad.stderr


def test_runner_fails_fast_when_the_gateway_is_unreachable():
    """Port 9 (discard) on loopback is closed on any sane host; the runner must
    exit 3 without starting pytest rather than letting the gate skip everything."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(
            os.environ,
            WILDFRAME_GATEWAY_URL="https://127.0.0.1:9",
            INTEGRATION_JUNIT=str(Path(tmp) / "junit.xml"),
        )
        proc = subprocess.run(
            ["bash", str(RUNNER)], capture_output=True, text=True, env=env, timeout=60
        )
        assert proc.returncode == 3, proc.stdout + proc.stderr
        assert not (Path(tmp) / "junit.xml").exists(), "pytest ran against a dead gateway"
    assert "INTEGRATION_SUITE_RESULT=UNREACHABLE" in proc.stdout
