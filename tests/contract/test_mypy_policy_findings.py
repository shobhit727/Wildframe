"""Contract guard: the mypy policy is real, and CI actually applies it (#971).

#971 was filed as a bookkeeping problem -- "55 unused type-ignore comments plus
7 no-any-return and 1 unreachable under the root policy". Those counts were
produced by running mypy against the *root* ``[tool.mypy]`` block. Two facts
make that framing wrong in a way that matters more than the count:

1. **The root policy was not being applied to anything CI runs.** The CI step is
   ``cd "services/$svc" && poetry run mypy app --config-file pyproject.toml``.
   ``--config-file`` names exactly one file, so the effective config for a
   service is ``services/<svc>/pyproject.toml``. mypy does *not* walk up to a
   parent ``pyproject.toml``, and a config file with no ``[tool.mypy]`` table
   falls back to mypy's built-in defaults -- not to the root block. Verified
   behaviourally, not assumed::

       # /tmp/probe.py ->  x: int = "s"
       #                    y: int = 1  # type: ignore[assignment]

       mypy /tmp/probe.py --config-file services/api-gateway/pyproject.toml
         -> p.py:1: error: ... [assignment]                      (no unused-ignore)
       mypy /tmp/probe.py --config-file services/auth-service/pyproject.toml
         -> p.py:1: error: ... [assignment]                      (no unused-ignore)
       mypy /tmp/probe.py --config-file <repaired root pyproject.toml>
         -> p.py:1: error: ... [assignment]
         -> p.py:2: error: Unused "type: ignore" comment  [unused-ignore]

   So ``warn_unused_ignores = true`` was written down and read by nobody. The
   unused-ignore class in #971 is not 55 stale comments to delete; it is a
   diagnostic nobody was emitting.

2. **The root file is not valid TOML.** Commit d5fadb23 ("resolve stash
   conflict") removed the line ``exclude = [`` and left a 132-element array
   literal orphaned after the last key/value pair in ``[tool.mypy]``. Every
   TOML consumer now rejects the file:

       $ python3 -m pytest tests/contract -q
       ERROR: pyproject.toml: Expected '=' after a key in a key/value pair
              (at line 153, column 45)
       exit 4

   That kills the root pytest configuration outright -- the whole
   ``tests/contract/`` suite cannot be collected, and the policy is unreadable
   by mypy, by Poetry, and by anything else that parses the file.

So this file asserts the *rule*, not a count:

* the root manifest is parseable at all (test 1);
* the root ``[tool.mypy]`` still carries its enforcement flags, by value, so a
  silent weakening to ``false`` is caught (test 2);
* CI's mypy invocation is exactly one site, is not wrapped in
  ``continue-on-error`` / ``|| true``, and the config file it points at is
  discovered from the workflow rather than assumed (test 3);
* the config CI *actually reads* enables ``warn_unused_ignores`` for every
  service it type-checks (test 4) -- the load-bearing one;
* no first-party module is silenced wholesale by a config CI reads (test 5);
* the checker itself rejects a missing or empty config (test 6), so none of the
  above can pass vacuously.

Deliberately absent: a count of unused type-ignores. #971's own numbers are a
moving target -- the same policy is being extended to eleven services that
currently have no ``[tool.mypy]`` block at all, which changes the diagnostic
set -- and a test that pins a count has to be edited every time it fails. That
is how a test stops being evidence. Test 4 is what makes the count go to zero
for the right reason.

Also deliberately absent: a check on the root file's 15
``services.<name>_service.*`` overrides. They carry ``ignore_errors = true``
but match nothing, because the directories are hyphenated
(``services/admin-service``) and the overrides are underscored. mypy says so
itself in the ``unused section(s)`` note. Asserting on provably inert text
would buy a permanently red test and teach the next person to edit it; those
overrides are worth deleting as cleanup, not worth a gate.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ROOT_MANIFEST = REPO / "pyproject.toml"
WORKFLOW = REPO / ".github" / "workflows" / "ci-cd.yml"
SERVICES_DIR = REPO / "services"

#: The flags that make #971's diagnostic classes detectable at all. Each maps
#: to a class of finding the issue counted:
#:
#: ==================  =========================================================
#: flag                findings it is the only thing that can report
#: ==================  =========================================================
#: warn_unused_ignores unused ``# type: ignore[...]`` comments
#: warn_return_any     returning ``Any`` from a function declared to return a
#:                     concrete type
#: warn_unreachable     statements mypy proved can never execute
#: warn_redundant_casts casts to the type a value already has
#: check_untyped_defs   bodies of unannotated functions
#: no_implicit_optional ``Optional`` without an explicit default
#: strict_optional      the ``None`` half of every union
#: ==================  =========================================================
#:
#: Listed by name and required to be exactly ``True``. Absent and ``False``
#: are treated identically, because for every one of these the mypy default is
#: off -- deleting a line and setting it to ``false`` are the same regression.
ENFORCEMENT_FLAGS: tuple[str, ...] = (
    "warn_unused_ignores",
    "warn_return_any",
    "warn_unreachable",
    "warn_redundant_casts",
    "check_untyped_defs",
    "no_implicit_optional",
    "strict_optional",
)

#: First-party import roots. A per-module override targeting one of these with
#: ``ignore_errors = true`` silences the service's own source, which is the
#: escape hatch AGENTS.md 18 forbids. Third-party stubs (``jose.*``,
#: ``aiokafka.*``, ``redis.*``, ``bcrypt.*``, ``stripe.*``, ``pyotp.*``,
#: ``deprecated.*``) are excluded: suppressing a missing-stub error is the
#: normal reason an override exists at all, and singling out those packages
#: here would be an arbitrary allowlist that rots as dependencies are added.
FIRST_PARTY_ROOTS: tuple[str, ...] = ("app", "app.", "services", "packages", "tests", "wildframe")

#: The whole argument string of the mypy command, to end of line.
#:
#: ``args`` is captured whole rather than only up to ``--config-file <cfg>``
#: because of a real hole found by mutation testing: an earlier regex ended at
#: the config value, so ``mypy app --config-file pyproject.toml || true``
#: matched as ``mypy app --config-file pyproject.toml`` and the softening check
#: never saw the escape hatch. A guard that cannot see the failure mode it
#: names is worse than no guard, because the docstring then claims coverage
#: nobody has. M6 in the mutation run is that case.
MYPY_COMMAND_RE = re.compile(r"mypy\s+app\b(?P<args>[^\n]*)")
CONFIG_FILE_RE = re.compile(r"--config-file\s+(?P<cfg>\S+)")
#: Backslash line continuations, so a command split across lines is matched as
#: one command and a trailing ``|| true`` cannot hide on the next line.
LINE_CONTINUATION_RE = re.compile(r"\\\s*\n\s*")


def _read_mypy_table(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``([tool.mypy] table, problem)`` for one config file.

    ``problem`` is a human-readable string when the table is unusable, and
    ``None`` otherwise. The three unusable cases are kept distinct on purpose,
    because they have different owners and different fixes:

    * the file does not exist -- the CI step points at nothing;
    * the file is not valid TOML -- d5fadb23's orphaned array, which is what
      ``test_root_manifest_is_valid_toml`` reports at the root;
    * the file parses but has no ``[tool.mypy]`` -- not an error by itself, it
      just means mypy's defaults apply, which is what test 4 exists to catch.
    """
    if not path.is_file():
        return None, f"{path} does not exist"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return None, f"{path} is not valid TOML: {exc}"
    table = data.get("tool", {}).get("mypy")
    if table is None:
        return None, f"{path} has no [tool.mypy] table, so mypy defaults apply"
    if not isinstance(table, dict):
        return None, f"{path} [tool.mypy] is a {type(table).__name__}, not a table"
    return table, None


def _unenforced_flags(mypy: dict[str, Any] | None) -> list[str]:
    """Enforcement flags absent from, or not enabled in, ``mypy``.

    A ``None`` table is reported as *every* flag being unenforced. That is the
    honest reading of a service with no ``[tool.mypy]``: mypy's defaults apply,
    and not one of these flags is on by default. Returning the full list (as
    opposed to a special-cased error string) is what makes
    ``test_the_enforcement_check_rejects_absent_and_empty_configs`` able to
    assert the behaviour instead of taking it on trust.
    """
    if mypy is None:
        return list(ENFORCEMENT_FLAGS)
    return [flag for flag in ENFORCEMENT_FLAGS if mypy.get(flag) is not True]


def _workflow_data() -> dict[str, Any]:
    """Parse the workflow, or fail naming the file.

    A malformed ``ci-cd.yml`` makes ``yaml.safe_load`` raise a
    ``ParserError`` whose message is a bare excerpt of the file with no
    indication of which file was being read. Found by mutation testing: a
    mutation that stripped ``set -euo pipefail`` broke the surrounding
    block-scalar indentation, and three unrelated tests then failed with a
    bare ``yaml.parser.ParserError`` naming no file. Loud, but aimed at the
    wrong thing -- so the parse is wrapped and the path is put in the message.
    """
    try:
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        pytest.fail(
            f"{WORKFLOW.relative_to(REPO)} is not valid YAML, so the mypy "
            "invocation cannot be located and every policy check in this file "
            f"would be describing a step that cannot be read:\n\n  {exc}",
            pytrace=False,
        )
    assert isinstance(data, dict) and "jobs" in data, (
        f"{WORKFLOW.relative_to(REPO)} parsed but carries no `jobs` mapping; "
        "this file reads the mypy step out of it and cannot proceed"
    )
    return data


def _mypy_invocations() -> list[tuple[str, str, str, str]]:
    """Every ``mypy ... --config-file ...`` in the workflow.

    Yields ``(label, config path, invocation line, full run body)``. The
    invocation line is kept separate from the run body on purpose, and
    narrowing the softening check to it is load-bearing rather than tidy.

    The step is a ``run: |`` script, so the run body also contains cleanup
    lines -- here two ``find ... -delete 2>/dev/null || true`` calls whose
    whole job is to succeed-or-not-matter while purging stale bytecode before
    mypy runs. A first version of this file searched the entire body for
    ``|| true`` and went red on those two lines, blaming the mypy step for
    something the mypy step does not do. That is the "fails for the wrong
    reason" failure this repo keeps paying for: a red test whose message points
    at the wrong line trains people to add the escape hatch they think is
    missing. So only the matched ``mypy`` command is inspected for softening,
    never the surrounding script.

    Discovering the invocation from the workflow rather than hardcoding
    ``services/<svc>/pyproject.toml`` is what stops this file from passing
    vacuously: the defect it guards is precisely "the policy lives in a config
    nobody reads", so a test that assumed the config path would reproduce the
    bug it is meant to catch.
    """
    data = _workflow_data()
    found: list[tuple[str, str, str, str]] = []
    for job in (data.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        job_continue = bool(job.get("continue-on-error"))
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            # Continuations are joined before matching, so a mypy command
            # wrapped across lines is matched as one command and a softening
            # token at its end cannot slip onto the following line.
            run = LINE_CONTINUATION_RE.sub(" ", step.get("run") or "")
            match = MYPY_COMMAND_RE.search(run)
            if match is None:
                continue
            args = match.group("args")
            # A step- or job-level escape hatch is recorded in the label so
            # test 3 can reject it without having to know which of the two it
            # was set on.
            prefix = "JOB_CONTINUE_ON_ERROR " if job_continue else ""
            if step.get("continue-on-error"):
                prefix += "STEP_CONTINUE_ON_ERROR "
            label = prefix + (step.get("name") or "<unnamed>")
            cfg_match = CONFIG_FILE_RE.search(args)
            # A mypy invocation with no --config-file is still a policy site.
            # Reporting it with an empty config lets test 4 name the cause
            # instead of raising IndexError on a missing capture group.
            found.append((label, cfg_match.group("cfg") if cfg_match else "", args, run))
    return found


def _effective_config_for(service: str, cfg: str) -> Path:
    """The config file mypy will read for ``service``, given CI's ``cfg``.

    mypy resolves a relative ``--config-file`` against the process working
    directory, which the CI step has already changed to the service directory.
    """
    return (SERVICES_DIR / service / cfg).resolve()


def _service_names() -> list[str]:
    """Service directories CI iterates over (``app/main.py`` present)."""
    return sorted(p.name for p in SERVICES_DIR.iterdir() if (p / "app" / "main.py").is_file())


# --------------------------------------------------------------------------
# 1. Precondition: the root manifest parses at all.
# --------------------------------------------------------------------------


def test_root_manifest_is_valid_toml() -> None:
    """Every TOML consumer of the root manifest fails while this is red.

    This is the first test because it is upstream of all the others. While the
    root manifest is unparseable, ``[tool.mypy]`` is unreadable, the root
    ``[tool.pytest.ini_options]`` is unreadable, and ``python3 -m pytest
    tests/contract`` cannot even collect -- so none of the remaining tests in
    ``tests/contract/`` are running at all.

    Introduced by d5fadb23, which deleted the line ``exclude = [`` from the
    end of ``[tool.mypy]`` and left a 132-element array literal orphaned after
    the last key/value pair. The one-line repair is to restore that key.
    """
    try:
        data = tomllib.loads(ROOT_MANIFEST.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        pytest.fail(
            f"{ROOT_MANIFEST.relative_to(REPO)} does not parse as TOML, so the "
            f"mypy policy, the pytest configuration and the package metadata are "
            f"all unreadable:\n\n  {exc}\n\n"
            "A TOML table cannot contain a bare array after a key/value pair; an "
            "orphaned array like this is what deleting the `exclude = [` key line "
            "leaves behind. See #971.",
            pytrace=False,
        )
    assert "project" in data or "tool" in data, (
        f"{ROOT_MANIFEST.relative_to(REPO)} parsed but carries no project or tool "
        "table; this is not the manifest the rest of this file assumes"
    )


# --------------------------------------------------------------------------
# 2. The root policy still enforces what it is supposed to.
# --------------------------------------------------------------------------


def test_root_mypy_policy_enables_every_enforcement_flag() -> None:
    """No flag in :data:`ENFORCEMENT_FLAGS` may be dropped or set to ``False``.

    Checked by value, not by presence: for each of these the mypy default is
    off, so ``warn_return_any = false`` and a deleted ``warn_return_any`` are
    the same regression and must read the same.
    """
    table, problem = _read_mypy_table(ROOT_MANIFEST)
    if problem is not None:
        pytest.fail(
            f"cannot read the root mypy policy -- {problem}\n\n"
            "Every flag below is therefore unenforced, because none of them is "
            "on by default in mypy:\n"
            + "".join(f"  - {flag}\n" for flag in _unenforced_flags(table))
            + "\nFix the root manifest first; see #971.",
            pytrace=False,
        )
    missing = _unenforced_flags(table)
    assert not missing, (
        "the root [tool.mypy] policy no longer enables these flags, and every one "
        "of them is off by default in mypy -- setting one to false is the same "
        "regression as deleting it:\n  " + "\n  ".join(missing) + "\n\nSee #971."
    )


# --------------------------------------------------------------------------
# 3. CI applies the policy, and does not soften the step.
# --------------------------------------------------------------------------


def test_ci_type_checks_each_service_with_one_unsoftened_mypy_step() -> None:
    """Exactly one mypy site, not marked ``continue-on-error``, not ``|| true``.

    AGENTS.md 18: "Do not add continue-on-error, || true, blanket test skips, or
    broad exception swallowing just to make validation green." A second mypy
    step is how that happens without anyone editing the first one, and it is
    also how a laxer policy would quietly arrive -- so the count is asserted,
    not just the presence.
    """
    invocations = _mypy_invocations()
    assert invocations, (
        f"no `mypy app --config-file ...` invocation found in "
        f"{WORKFLOW.relative_to(REPO)}; the type-checking step this file guards "
        "has been renamed or removed, and every policy check below would then "
        "be describing a job that does not run"
    )
    assert len(invocations) == 1, (
        "expected exactly one mypy invocation, found "
        f"{len(invocations)}: {[label for label, _, _, _ in invocations]}. A second "
        "site can carry a laxer policy and the laxer one wins whenever it is the "
        "step someone watches."
    )

    name, _, args, run = invocations[0]
    # Scoped to `args` -- the mypy command's own argument string -- not `run`.
    # See _mypy_invocations: the run body legitimately contains
    # `find ... || true` cleanup, and blaming the mypy step for it is a failure
    # message that sends the next reader to the wrong line. `args` also spans
    # the whole command, so a `|| true` appended after `--config-file <path>`
    # is inside the window; an earlier version that stopped at the config path
    # missed exactly that and mutation M6 caught it.
    softeners = [
        token
        for token in ("continue-on-error", "|| true", "|| :", "; true", "exit 0")
        if token in args
    ]
    assert "CONTINUE_ON_ERROR" not in name, (
        f"the mypy step {name!r} is marked continue-on-error, so it cannot fail "
        "the build. See #971."
    )
    assert not softeners, (
        f"the mypy step tolerates failure via {softeners}; a type-checking step "
        "that cannot fail is not a policy. See #971."
    )
    assert "set -euo pipefail" in run, (
        "the mypy step no longer sets `set -euo pipefail`, so a failure in the "
        "per-service loop does not stop the loop or the job. See #971."
    )


# --------------------------------------------------------------------------
# 4. The load-bearing check: the config CI reads must enforce.
# --------------------------------------------------------------------------


def test_every_type_checked_service_enables_unused_ignore_detection() -> None:
    """The config CI actually passes to mypy must set ``warn_unused_ignores``.

    This is the check that #971 needed and did not have. It is written against
    the config path discovered from the workflow, so it fails if CI is
    repointed at a config that does not enforce, and it fails if a service
    gains a ``[tool.mypy]`` table that does not carry the flag.

    It is a *rule*, not a count, on purpose. Eleven services currently have no
    ``[tool.mypy]`` table at all; while those are being filled in the
    diagnostic set moves, and pinning a number here would mean editing this
    test every time it fails.
    """
    _, cfg, _, _ = _mypy_invocations()[0]
    services = _service_names()
    assert services, f"no service found under {SERVICES_DIR.relative_to(REPO)}"

    unreached: list[str] = []
    for service in services:
        path = _effective_config_for(service, cfg)
        table, problem = _read_mypy_table(path)
        if problem is not None:
            unreached.append(f"  services/{service} -- {problem}")
            continue
        if table.get("warn_unused_ignores") is not True:
            unreached.append(
                f"  services/{service} -- {path.relative_to(REPO)} does not set "
                f"warn_unused_ignores = true"
            )

    assert not unreached, (
        "CI type-checks these services with a config that cannot report an "
        "unused `# type: ignore` comment, which is the whole class of finding "
        "#971 counted. mypy takes exactly one config file and does not fall "
        "back to the root manifest, so the root policy is not applied here "
        "either way:\n"
        + "\n".join(unreached)
        + "\n\nSet `warn_unused_ignores = true` in each service's [tool.mypy], or "
        "point the CI step at a config that has it. See #971."
    )


# --------------------------------------------------------------------------
# 5. No first-party wholesale silencing in a config CI reads.
# --------------------------------------------------------------------------


def test_no_config_ci_reads_silences_first_party_code() -> None:
    """``ignore_errors`` on a first-party module is a blanket escape hatch.

    Scoped to the config files CI reads, not the root manifest, because only
    those are load-bearing: the root manifest's ``services.<name>_service.*``
    overrides match nothing (hyphenated directories, underscored patterns) and
    are inert, so gating on them would be gating on decoration.

    A config this loop cannot read is reported as an offender rather than
    skipped. That distinction was found by mutation testing: an earlier
    version did ``if table is None: continue``, and a service config that had
    become invalid TOML -- which is what appending a second ``[tool.mypy]``
    table produces -- sailed through this test silently while the silencing it
    was added to detect went unexamined. A guard that quietly stops looking is
    the failure mode AGENTS.md 19.1 is about, so an unreadable config is a
    failure here, and it says which service and why.
    """
    _, cfg, _, _ = _mypy_invocations()[0]
    offenders: list[str] = []
    for service in _service_names():
        path = _effective_config_for(service, cfg)
        table, problem = _read_mypy_table(path)
        if table is None:
            offenders.append(f"  services/{service} -- cannot be examined: {problem}")
            continue
        for override in table.get("overrides") or []:
            if override.get("ignore_errors") is not True:
                continue
            module = override.get("module")
            patterns = module if isinstance(module, list) else [module]
            for pattern in patterns:
                if not isinstance(pattern, str):
                    continue
                head = pattern.split(".")[0]
                if any(pattern.startswith(root) for root in FIRST_PARTY_ROOTS) or head in {
                    r.rstrip(".") for r in FIRST_PARTY_ROOTS
                }:
                    offenders.append(
                        f"  services/{service} -- {path.relative_to(REPO)} sets "
                        f"ignore_errors = true for {pattern!r}"
                    )
    assert not offenders, (
        "a config CI reads either silences type checking for first-party code, "
        "or could not be examined at all. A per-module `ignore_errors = true` "
        "on a first-party namespace is a blanket escape hatch rather than a stub "
        "suppression, and an unreadable config means this check was not looking "
        "at all:\n" + "\n".join(offenders) + "\n\nSee #971."
    )


# --------------------------------------------------------------------------
# 6. Anti-vacuity: the checker above must reject an absent or empty config.
# --------------------------------------------------------------------------


def test_the_enforcement_check_rejects_absent_and_empty_configs() -> None:
    """:func:`_unenforced_flags` must fail closed, so tests 2/4 cannot pass by accident.

    Every other check here reads a config off disk and asserts something about
    it. If :func:`_unenforced_flags` returned ``[]`` for a missing or empty
    table, a config that had lost its entire ``[tool.mypy]`` table would look
    identical to a correct one and every policy test above would pass against
    nothing. This is the test that makes those tests mean something, and it
    needs no repository state to run.
    """
    assert _unenforced_flags(None) == list(ENFORCEMENT_FLAGS), (
        "a missing [tool.mypy] table must report every enforcement flag as "
        "unenforced -- mypy's defaults enable none of them"
    )
    assert _unenforced_flags({}) == list(ENFORCEMENT_FLAGS), (
        "an empty [tool.mypy] table must report every enforcement flag as " "unenforced"
    )

    # A flag present but disabled is the same regression as a flag absent.
    weakened = dict.fromkeys(ENFORCEMENT_FLAGS, True)
    for value in (False, None, "true", 1):
        weakened["warn_unused_ignores"] = value
        assert "warn_unused_ignores" in _unenforced_flags(weakened), (
            f"warn_unused_ignores = {value!r} must be treated as unenforced; only "
            "the boolean true enables it, and mypy's own config parser requires "
            "a real boolean"
        )

    # And a complete, correct table must report nothing.
    complete = dict.fromkeys(ENFORCEMENT_FLAGS, True)
    assert _unenforced_flags(complete) == [], (
        "a table enabling every enforcement flag must report none as missing, "
        "otherwise the check is not a check"
    )


def test_a_missing_config_file_is_reported_not_treated_as_empty() -> None:
    """:func:`_read_mypy_table` must distinguish 'no file' from 'no table'.

    Both mean "not enforced", but only one of them is a typo in the CI step. A
    reader that returned ``({}, None)`` for a nonexistent path would make test 4
    report the wrong owner for the wrong failure.
    """
    missing = REPO / "services" / "__no_such_service__" / "pyproject.toml"
    table, problem = _read_mypy_table(missing)
    assert table is None, f"a nonexistent config must not yield a table, got {table!r}"
    assert (
        problem is not None and "does not exist" in problem
    ), f"a nonexistent config must say so explicitly, got {problem!r}"
    assert _unenforced_flags(table) == list(
        ENFORCEMENT_FLAGS
    ), "a nonexistent config must still be reported as fully unenforced"
