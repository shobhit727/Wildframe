"""Contract guard against vacuous tests (issue #908).

A test that asserts nothing is not a weak test -- it is a *decorated* one. It
reports green after a refactor that deleted the behaviour under test, and it
costs the same CI time as a test that would have caught it. AGENTS.md §19.1 is
the rule; this file is the part that can be checked mechanically.

WHAT THIS GUARD DELIBERATELY DOES NOT DO
-----------------------------------------
It does not assert "the repo contains no vacuous tests". A count-only gate
fails in two bad ways at once: fixing the backlog in one commit makes the gate
red for reasons unrelated to the commit, and nobody can tell a real regression
from a drifting baseline. So this guard pins **the root `tests/` tree to zero**
and asserts the *detector itself* still works. The service-local backlog is
tracked separately and is deliberately not gated here -- those paths belong to
other owners, and a gate that another team cannot move without editing this file
is a gate that gets deleted.

THE MEASUREMENT, AND WHY THE ISSUE'S NUMBER WAS HIGHER
------------------------------------------------------
Issue #908 reported "184 of 1,388 backend tests (13.3%) assert nothing". That
figure is reproducible only as a *token* scan -- counting every test whose body
contains no literal ``assert``. On this branch that scan reports **817 of
6,967 (11.7%)**, almost exactly the issue's ratio. It is not a count of tests
that cannot fail, because ``assert`` is not the only assertion syntax in use:

  * 602 of those 817 assert via ``pytest.raises`` / ``pytest.fail``
  * 145 assert via ``unittest.mock`` (``mock.assert_awaited_once()``) or
    ``unittest.TestCase`` (``self.assertEqual``)
  * 10 assert through a same-module helper
  * only 60 assert nothing at all

``svc.subscribe.assert_not_awaited()`` raises ``AssertionError`` like any other
assert. Reporting those as vacuous would produce a large, alarming and wrong
list -- so ``scripts/find_vacuous_tests.py`` recognises them, and this guard
fails if that recognition is ever removed. The honest count is **0.9%, not
13.3%**: the over-count was the headline, and it is why the real defects got
ignored.

Redundancy note: several of the 60 are ``await service.thing()`` with no
assert, where the test name promises something ("..._is_accepted") that only
the absence of an exception would establish. Fixing one is a one-line change,
but fixing them is the owning team's call, not this guard's.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DETECTOR = REPO / "scripts" / "find_vacuous_tests.py"

#: The root suite this repo owns outright. Must stay at zero.
OWNED_ROOT = REPO / "tests"


def _load_detector():
    """Import the detector by path, registering it in sys.modules first.

    The module defines a frozen dataclass, and ``dataclasses`` looks the class
    up via ``sys.modules[cls.__module__]`` while building it. An
    ``importlib`` module that was never registered has no ``sys.modules`` entry,
    so loading it that way raises ``AttributeError: 'NoneType' object has no
    attribute '__dict__'`` rather than anything to do with detection.
    """
    spec = importlib.util.spec_from_file_location("wildframe_find_vacuous", DETECTOR)
    assert spec and spec.loader, f"cannot load detector from {DETECTOR}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_detector_script_exists_and_is_documented() -> None:
    """A detector nobody can run is not a detector.

    Checks the things a reader needs before trusting a number: the file is
    present, and it carries the usage comment AGENTS.md §19.1b requires of
    every script in `scripts/`.
    """
    assert DETECTOR.exists(), f"{DETECTOR} is missing -- issue #908 ships no measurement tool"
    source = DETECTOR.read_text(encoding="utf-8")
    # A shebang is expected and allowed on an executable script; what matters is
    # that the first *code* is the usage docstring, not an import or a comment.
    first_line = ""
    for line in source.splitlines():
        if line.strip() and not line.startswith("#!"):
            first_line = line.strip()
            break
    assert first_line.startswith(
        '"""'
    ), f"the script's first statement must be its usage docstring, got {first_line[:60]!r}"
    for section in ("USAGE", "EXIT CODES"):
        assert section in source, f"the usage docstring does not document {section}"


def test_detector_self_test_passes() -> None:
    """The detector must still detect before its verdict means anything.

    A vacuous-test detector that has silently stopped detecting reports "0
    vacuous tests", which reads as good news. That is the exact failure this
    guard exists to prevent, so the detector's own known-bad/known-good suite is
    run here rather than trusted.
    """
    detector = _load_detector()
    assert (
        detector.main(["--self-test"]) == 0
    ), "scripts/find_vacuous_tests.py --self-test failed; its findings are not evidence"


def test_owned_root_tests_contain_no_vacuous_tests() -> None:
    """The root `tests/` suite is pinned at zero vacuous tests.

    Scoped deliberately narrow. The service-local backlog is real but belongs to
    other owners, and a baseline that needs editing whenever anyone fixes a
    test is a baseline that stops being read.
    """
    detector = _load_detector()
    result = detector.scan([OWNED_ROOT])

    findings = [f"{f.location}: {f.name} [{f.severity}]" for f in result.findings]
    assert not findings, (
        f"{len(findings)} vacuous test(s) in tests/, which this guard pins at zero:\n  "
        + "\n  ".join(findings)
        + "\n\nA test that asserts nothing cannot fail. Add the missing assertion, or "
        "delete the test -- do not relax this guard."
    )


def test_root_suite_is_actually_being_scanned() -> None:
    """Guard the guard: a scan that finds no tests also finds no vacuous tests.

    The assertion above passes vacuously on an empty scan -- the same failure
    mode it is meant to catch. This asserts the scan really read the tree.
    """
    detector = _load_detector()
    result = detector.scan([OWNED_ROOT])

    assert result.scanned_files >= 20, (
        f"only {result.scanned_files} test files found under {OWNED_ROOT}; "
        "the scan is not seeing the tree and would pass for the wrong reason"
    )
    assert result.test_functions >= 150, (
        f"only {result.test_functions} test functions found under {OWNED_ROOT}; "
        "expected well over 150, so the denominator is wrong"
    )


def test_detector_recognises_mock_and_pytest_assertions() -> None:
    """Pins the decision that makes #908's 13.3% wrong.

    ``svc.assert_not_awaited()`` and ``pytest.raises(...)`` are assertions. If a
    future edit drops either from the detector, the reported vacuous count jumps
    by ~750 and the number stops meaning "tests that cannot fail". This asserts
    the recognition directly against synthetic source, independent of whatever
    happens to exist in the tree today.
    """
    detector = _load_detector()
    sample = (
        "async def test_mock_style():\n"
        "    svc.thing.assert_not_awaited()\n"
        "async def test_unittest_style():\n"
        "    self.assertEqual(1, 1)\n"
        "def test_raises():\n"
        "    with pytest.raises(ValueError):\n"
        "        boom()\n"
        "def test_fails():\n"
        "    pytest.fail('nope')\n"
        "def test_genuinely_vacuous():\n"
        "    await service.thing()\n"
    )
    fixture_dir = REPO / "tests" / "__vacuous_fixture__"
    fixture_dir.mkdir(exist_ok=True)
    fixture_path = fixture_dir / "test_synthetic.py"
    try:
        fixture_path.write_text("import pytest\n" + sample, encoding="utf-8")
        result = detector.scan([fixture_dir])
        reported = {f.name for f in result.findings}
        assert reported == {"test_genuinely_vacuous"}, (
            f"expected only the assertion-free test to be reported, got {sorted(reported)}. "
            "If mock/unittest/pytest.raises assertions are no longer recognised, "
            "#908's count will inflate by several hundred."
        )
    finally:
        fixture_path.unlink(missing_ok=True)
        fixture_dir.rmdir()
