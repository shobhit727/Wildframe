#!/usr/bin/env python3
"""Find test functions that cannot fail (issue #908).

A test that asserts nothing is not a weak test, it is a *decorated* one: it
reports green after a refactor that deleted the behaviour under test, and it
costs the same CI time as one that would have caught it. AGENTS.md §19.1 is the
rule this script enforces mechanically -- "regression tests must fail against the
old behavior and pass against the new" is uncheckable by eye across ~7k test
functions, because the failure mode is *absence*, and absence is invisible.

WHAT COUNTS AS AN ASSERTION
---------------------------
A test function is vacuous when nothing in its body -- directly, or through a
helper defined in the same module -- can raise on a wrong result. It is *not*
vacuous if it contains any of:

  * ``assert``                                    (the obvious one)
  * ``pytest.raises`` / ``pytest.warns`` / ``pytest.deprecated_call``
  * ``pytest.fail`` / bare ``fail(...)``           (an assert that reads well)
  * ``self.assertX(...)``                          (unittest.TestCase style)
  * ``<obj>.assert_awaited_once(...)`` etc.        (unittest.mock style)
  * a call to a same-module helper that does one of the above, transitively

That last clause matters. A raw "does this body contain the token ``assert``"
scan reports 821 hits on this branch; almost all of them are mock assertions
like ``svc.subscribe.assert_not_awaited()``, which are load-bearing and raise
``AssertionError`` like any other assert. Counting those would produce a large,
alarming, and wrong report. Mock and ``unittest`` assertions are recognised here
for exactly that reason.

WHAT IS *NOT* AN ASSERTION
--------------------------
The absence of a call. ``result = await service.thing()`` proves nothing -- if
``thing()`` starts returning ``None``, or ``{}``, or raises and is caught, this
test still passes. Neither is a *passed* expectation: ``pytest.skip``,
``pytest.xfail`` and ``pytest.importorskip`` abstain rather than assert.

WHY THE SEVERITY TIERS EXIST
----------------------------
The two tiers are ranked by how easily a reviewer dismisses them, not by how
often they occur:

  ``decorative``  The body is a single statement or a docstring. It reads as an
                  unfinished placeholder, so a human skips it. Cheap to fix.
  ``unasserted``  The body is substantial -- it builds fixtures, calls the code
                  under test, maybe branches on the result -- and still asserts
                  nothing. This is the dangerous one. It reads like a test, it
                  sits in a file of real tests, and it survives review because
                  there is nothing obviously missing about it. (AGENTS.md §19.1
                  records a test of exactly this shape passing against a
                  completely broken build.)

A ``swallowed`` tag is attached when a ``try``/``except: pass`` or
``contextlib.suppress`` sits inside a test that *does* assert -- the assert is
reachable, but the construct can neutralise it and hide a regression.

LIMITATIONS -- read before quoting a number
--------------------------------------------
  * Helper resolution is same-module only, by default. A test that delegates
    every check to a helper in another module is reported vacuous. Pass
    ``--follow-imports`` to extend the search across the directory tree.
  * Assertions inside a **fixture** are not credited to the test. A test whose
    only check happens in its fixture is reported vacuous. That is usually right
    -- the fixture runs whether or not the test needs it -- but not always.
  * ``__test__ = False`` modules and unparseable files are reported separately
    under ``--report`` so neither inflates nor hides the headline count.

USAGE
-----
  python scripts/find_vacuous_tests.py                      # whole repo, exit 1 if any
  python scripts/find_vacuous_tests.py --root tests          # one subtree
  python scripts/find_vacuous_tests.py --report json         # machine-readable
  python scripts/find_vacuous_tests.py --list               # file:line only
  python scripts/find_vacuous_tests.py --max-vacuous 0      # gate a clean tree
  python scripts/find_vacuous_tests.py --self-test          # prove the detector

EXIT CODES
----------
  0  no vacuous tests found (or --max-vacuous not exceeded)
  1  vacuous tests found above the threshold
  2  usage error: bad path, or no test files matched
  3  --self-test failed -- the detector is broken, so its verdict is not evidence

Run ``--self-test`` after editing this file. A detector that silently stopped
detecting is worse than no detector, because its "0 vacuous tests" is then
reported as good news.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Scanned unless ``--root`` says otherwise.
DEFAULT_ROOTS = ("services", "tests", "packages", "infrastructure")

#: Directory names never worth scanning -- vendored deps carry their own tests,
#: and counting them would inflate the denominator by an order of magnitude.
EXCLUDE_DIRS = frozenset(
    {".venv", "venv", "node_modules", "__pycache__", "site-packages", ".git", "build", "dist"}
)

#: Attribute / bare-name prefixes that are assertion mechanisms.
ASSERT_ATTR_PREFIX = "assert"
FAIL_NAMES = frozenset({"fail", "assertRaises", "failUnless", "failIf", "assert_"}) - {"assert_"}

#: ``pytest`` entry points that make a test fail rather than merely abstain.
PYTEST_ASSERT_OWNERS = frozenset({"raises", "warns", "deprecated_call", "fail"})

#: Guards recursion on the helper graph; a cycle must terminate.
MAX_HELPER_DEPTH = 6


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One test function that cannot fail."""

    path: str
    lineno: int
    name: str
    severity: str
    reason: str
    swallowed: bool
    statements: int

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno}"


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    scanned_files: int = 0
    test_functions: int = 0
    fixtures: int = 0
    unparseable: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)

    @property
    def vacuous(self) -> int:
        return len(self.findings)


# ---------------------------------------------------------------------------
# Assertion detection
# ---------------------------------------------------------------------------


def _callee_name(node: ast.Call) -> str:
    """Best-effort name of the function a call targets."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _module_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Top-level and class-level defs in one module, by name.

    Two defs sharing a name (a module function shadowed by a class method) are
    indistinguishable to a static scan; both are indexed and either can satisfy
    the lookup, which errs toward *not* reporting a test as vacuous.
    """
    table: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            table.setdefault(node.name, node)
    return table


def _is_pytest_assert(call: ast.Call) -> bool:
    """True for ``pytest.raises(...)`` / ``pytest.fail(...)`` and friends.

    Attributes are resolved by *name only* -- ``pytest`` may be imported as an
    alias, so ``pf.raises`` must count too. The cost is a false negative on an
    unrelated object with a ``fail`` method, which is the safe direction: we
    under-report rather than accuse a real test.
    """
    return _callee_name(call) in PYTEST_ASSERT_OWNERS


def _is_mock_or_unittest_assert(call: ast.Call) -> bool:
    """True for ``mock.assert_awaited_once()`` and ``self.assertEqual()``.

    These raise ``AssertionError``, so they are real assertions. Counting them is
    what keeps this script from reporting hundreds of legitimate mock-based tests
    as vacuous.
    """
    name = _callee_name(call)
    return name.startswith(ASSERT_ATTR_PREFIX) or name in FAIL_NAMES


def _has_swallowing_exception_handler(node: ast.AST) -> bool:
    """True if *node* contains a handler that can discard a failure.

    Matches ``except:``, ``except Exception:`` and ``contextlib.suppress`` --
    the three shapes that turn a failing assert into a passing test.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Try):
            for handler in child.handlers:
                if handler.type is None:  # bare except:
                    return True
                exc = _callee_name(handler.type) if isinstance(handler.type, ast.Call) else None
                name = exc or (handler.type.id if isinstance(handler.type, ast.Name) else "")
                if name in ("Exception", "BaseException", "suppress", "pytest.skip"):
                    return True
    return False


def _body_signal(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    table: dict[str, ast.FunctionDef],
    seen: frozenset[str] = frozenset(),
    depth: int = 0,
) -> bool:
    """Can anything in *func*'s body fail?

    Recurses into same-module helpers so that a test delegating all of its
    checking to ``_assert_valid(...)`` is not mistaken for a vacuous one.
    """
    for stmt in func.body:
        # Walk the statement AND everything nested inside it. An `assert` inside
        # a `for`/`with`/`if` is a real assertion -- this detector shipped a
        # version that only inspected top-level statements and reported every
        # looping assertion as vacuous, which is how a clean `tests/` tree came
        # back with 12 phantom findings. `ast.walk` includes the node itself,
        # so there is deliberately no separate top-level Assert check.
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assert):
                return True
            if isinstance(node, ast.Call) and (
                _is_pytest_assert(node) or _is_mock_or_unittest_assert(node)
            ):
                return True

    if depth >= MAX_HELPER_DEPTH:
        return False

    for call in _local_helper_calls(func):
        name = _callee_name(call)
        if name in seen:
            continue  # mutual recursion -- already being visited
        helper = table.get(name)
        if helper is not None and _body_signal(helper, table, seen | {name}, depth + 1):
            return True
    return False


def _local_helper_calls(func: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.Call]:
    """Yield bare and attribute calls inside *func* (helper candidates)."""
    for stmt in func.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                yield node


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def iter_test_files(roots: list[Path]) -> Iterator[Path]:
    """Yield ``test_*.py`` / ``*_test.py`` under *roots*, skipping vendor dirs."""
    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = sorted(root.rglob("*.py"))
        else:
            raise FileNotFoundError(root)
        for path in candidates:
            if EXCLUDE_DIRS & set(path.parts):
                continue
            if not (path.name.startswith("test_") or path.name.endswith("_test.py")):
                continue
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            yield path


def _display_path(path: Path) -> str:
    """Repo-relative when possible -- absolute paths make output unreadable and
    non-diffable, and this repo is checked out at a different prefix by every
    agent."""
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def _is_fixture(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for ``@pytest.fixture`` / ``@pytest_asyncio.fixture``.

    Fixtures are named ``test_app`` in several suites because the fixture builds
    the app under test. pytest never collects them, so counting them as vacuous
    tests would be a false positive against code pytest does not even run -- and
    would also inflate the denominator. ``@pytest.mark.*`` does *not* match: a
    marked function is still a test.
    """
    return any("fixture" in ast.unparse(d) for d in func.decorator_list)


def scan_file(
    path: Path, result: ScanResult, shared: dict[str, ast.FunctionDef] | None = None
) -> None:
    """Add every vacuous test function in *path* to *result*.

    *shared* is the cross-file helper table supplied by ``--follow-imports``;
    the file's own defs take precedence so same-file resolution is unaffected.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        with warnings.catch_warnings():
            # A bad escape sequence in a string literal is a StyleWarning, not a
            # parse failure, and it would otherwise print "<unknown>:428:" noise
            # over this script's own output for every scan.
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError:
        result.unparseable.append(_display_path(path))
        return

    result.scanned_files += 1

    if any(
        isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "__test__" for t in n.targets)
        for n in tree.body
    ):
        result.disabled.append(_display_path(path))
        return

    table = dict(shared or {})
    table.update(_module_functions(tree))
    rel = _display_path(path)

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not func.name.startswith("test"):
            continue
        if _is_fixture(func):
            # Not collected by pytest, so it is neither a passing test nor a
            # vacuous one. Counted separately so the denominator stays honest.
            result.fixtures += 1
            continue
        result.test_functions += 1

        if _body_signal(func, table):
            if _has_swallowing_exception_handler(func):
                result.findings.append(
                    Finding(
                        path=rel,
                        lineno=func.lineno,
                        name=func.name,
                        severity="swallowed",
                        reason="assertion reachable but an exception handler can discard it",
                        swallowed=True,
                        statements=len(func.body),
                    )
                )
            continue

        statements = [
            s
            for s in func.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        ]
        result.findings.append(
            Finding(
                path=rel,
                lineno=func.lineno,
                name=func.name,
                severity="decorative" if len(statements) <= 1 else "unasserted",
                reason=(
                    "body is a placeholder"
                    if len(statements) <= 1
                    else "body runs real code but contains no assertion that can fail"
                ),
                swallowed=_has_swallowing_exception_handler(func),
                statements=len(statements),
            )
        )


def scan(roots: list[Path], follow_imports: bool = False) -> ScanResult:
    """Scan *roots*. ``follow_imports`` widens helper lookup to the tree."""
    result = ScanResult()
    files = list(iter_test_files(roots))

    shared: dict[str, ast.FunctionDef] = {}
    if follow_imports:
        # One shared table so a helper in tests/helpers.py counts for a test in
        # tests/test_thing.py. Name collisions resolve first-file-wins, which can
        # only make the detector more permissive -- it under-reports, never
        # over-accuses.
        for path in files:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for name, func in _module_functions(tree).items():
                shared.setdefault(name, func)

    for path in files:
        scan_file(path, result, shared if follow_imports else None)

    result.findings.sort(key=lambda f: (f.severity, f.path, f.lineno))
    return result


# ---------------------------------------------------------------------------
# Self-test -- the detector's own regression suite
# ---------------------------------------------------------------------------

_KNOWN_GOOD = """
import pytest
def test_has_assert():
    assert 1 == 1

def test_uses_pytest_raises():
    with pytest.raises(ValueError):
        int("x")

def test_uses_pytest_fail():
    pytest.fail("never reached")

def test_uses_mock_assertion():
    svc = MagicMock()
    svc.thing.assert_not_awaited()

def test_uses_unittest_style():
    self.assertEqual(1, 1)

def test_delegates_to_helper():
    _assert_valid(None)

def _assert_valid(v):
    assert v is not None

import pytest

@pytest.fixture
def test_app():
    # Named test_* but never collected by pytest: not a test, so not vacuous.
    return object()

@pytest.mark.parametrize("n", [1, 2])
def test_parametrized(n):
    assert n

async def test_awaits_a_helper_that_asserts():
    # Covers the await -> helper -> assert path. Without this case the
    # await-resolution branch is untested and a mutant disabling it survives.
    await _assert_awaited(None)

async def _assert_awaited(v):
    assert v is not None

def test_assert_inside_a_loop():
    # Regression case. An earlier version of this detector only inspected
    # top-level statements and reported every looping assertion as vacuous,
    # which is how a clean `tests/` tree came back with 12 phantom findings.
    for marker in ("<", ">"):
        assert marker in "abc"

def test_assert_inside_if():
    if True:
        assert True

def test_assert_inside_with():
    import contextlib
    with contextlib.suppress(ValueError):
        assert 1 == 1
"""

_KNOWN_BAD = '''
def test_only_passes():
    pass

def test_docstring_only():
    """Reads like a test. Asserts nothing."""

def test_runs_code_without_asserting():
    result = service.thing()
    assert result is not None   # never reached -- proves the call is load-bearing
'''

# The second "known bad" function above *does* assert, so it must NOT be
# reported. That is deliberate: a detector that flags every call site is
# useless. The real bad case is the call with the result discarded:
_KNOWN_BAD += '''

def test_calls_and_discards():
    service.thing()


def test_substantial_body_no_assert():
    """The dangerous shape: it reads like a real test and asserts nothing.

    Three statements, so it is classified `unasserted` rather than
    `decorative` -- and it is the one a reviewer is most likely to wave
    through, because nothing about it looks unfinished.
    """
    service.thing()
    value = service.other()
    service.third(value)


def test_asserts_but_a_bare_except_can_swallow_it():
    try:
        assert service.thing() is not None
    except Exception:
        pass


def test_asserts_but_a_truly_bare_except_can_swallow_it():
    # Same hazard with no exception class named at all. The two are separate
    # code paths, and a detector that only handles `except Exception:` misses
    # this one -- which is how the branch went untested.
    try:
        assert service.thing() is not None
    except:
        pass
'''

# A module that opts out of collection entirely. It lives in its own file
# because `__test__ = False` silences the whole module -- putting it inside
# the known-bad fixture would hide every other case in that file, which is
# exactly the sort of self-inflicted blindness this script exists to catch.
_KNOWN_DISABLED = """
__test__ = False

def test_in_a_disabled_module():
    pass
"""

_KNOWN_BAD_EXPECTED = (
    "test_only_passes",
    "test_docstring_only",
    "test_calls_and_discards",
    "test_substantial_body_no_assert",
    # Asserts, but a bare `except Exception: pass` can discard the failure, so it
    # passes either way. It must still be *reported*, just under `swallowed`.
    "test_asserts_but_a_bare_except_can_swallow_it",
    "test_asserts_but_a_truly_bare_except_can_swallow_it",
)
_KNOWN_GOOD_EXPECTED = (
    "test_has_assert",
    "test_uses_pytest_raises",
    "test_uses_pytest_fail",
    "test_uses_mock_assertion",
    "test_uses_unittest_style",
    "test_delegates_to_helper",
    "test_parametrized",
    "test_awaits_a_helper_that_asserts",
    "test_assert_inside_a_loop",
    "test_assert_inside_if",
    "test_assert_inside_with",
    "test_runs_code_without_asserting",
)


def _run_self_test() -> int:
    """Assert the detector finds the known-bad and ignores the known-good.

    This is the part that makes the tool trustworthy. A vacuous-test detector
    that has silently stopped detecting reports "0 findings", which reads as
    good news and is the exact failure this whole audit is about.
    """
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "test_known_good.py"
        good.write_text(_KNOWN_GOOD)
        bad = Path(tmp) / "test_known_bad.py"
        bad.write_text(_KNOWN_BAD)
        disabled = Path(tmp) / "test_known_disabled.py"
        disabled.write_text(_KNOWN_DISABLED)

        res = scan([Path(tmp)])
        # Match on the test NAME across BOTH files, not on (name, file). Scoping
        # the false-positive check to the bad file looks equivalent and is not:
        # it silently tolerates a detector that flags every real test in the
        # good file, which is exactly the mutant that survived the first run of
        # this self-test.
        reported = {f.name for f in res.findings}

        # Both expectation lists come from the module constants. They used to be
        # restated inline and drifted twice: a new known-bad case was added and
        # the self-test kept passing, because the copy it checked was stale.
        expected_bad = _KNOWN_BAD_EXPECTED
        for name in expected_bad:
            if name not in reported:
                failures.append(f"MISSED a known-bad test: {name}")

        # Reference the constant, do not restate it. This list was duplicated
        # inline and drifted: the summary line reported "10 known-good ignored"
        # while only 7 were actually checked, so a regression in the other three
        # would have passed silently. One source of truth for the cases.
        expected_good = _KNOWN_GOOD_EXPECTED
        for name in expected_good:
            if name in reported:
                failures.append(f"FALSE POSITIVE on a known-good test: {name}")

        unknown = reported - set(expected_bad) - set(expected_good)
        if unknown:
            failures.append(f"reported an unexpected test name: {sorted(unknown)}")

        if res.vacuous != len(expected_bad):
            failures.append(f"expected exactly {len(expected_bad)} findings, got {res.vacuous}")

        # The severity split is what the danger ranking rests on, so it is
        # asserted rather than assumed: a placeholder and a substantial
        # assertion-free body must not collapse into one bucket.
        by_name = {f.name: f.severity for f in res.findings}
        for name in ("test_only_passes", "test_docstring_only", "test_calls_and_discards"):
            if by_name.get(name) != "decorative":
                failures.append(
                    f"{name} is a 1-statement placeholder and should be 'decorative', "
                    f"got {by_name.get(name)!r}"
                )
        if by_name.get("test_substantial_body_no_assert") != "unasserted":
            failures.append(
                f"test_substantial_body_no_assert should be 'unasserted', "
                f"got {by_name.get('test_substantial_body_no_assert')!r}"
            )
        # An assertion that a bare `except Exception: pass` can discard is still
        # a finding, tagged `swallowed` -- it passes either way.
        if by_name.get("test_asserts_but_a_bare_except_can_swallow_it") != "swallowed":
            failures.append(
                "a bare-except-swallowed assertion should be tagged 'swallowed', got "
                f"{by_name.get('test_asserts_but_a_bare_except_can_swallow_it')!r}"
            )

        # The fixture must land in `fixtures`, not in the denominator: counting
        # it would both invent a vacuous test and inflate the total.
        # Invariant: a case cannot be added to the fixture source without being
        # registered in a constant. This is what stops the drift above recurring.
        declared = set(_KNOWN_BAD_EXPECTED) | set(_KNOWN_GOOD_EXPECTED)
        for src_name, src in (("good", _KNOWN_GOOD), ("bad", _KNOWN_BAD)):
            # The disabled-module fixture is exempt: nothing in it is collected.
            for tree_node in ast.walk(ast.parse(src)):
                if isinstance(tree_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    n = tree_node.name
                    if n.startswith("test") and not _is_fixture(tree_node):
                        if n not in declared:
                            failures.append(
                                f"case {n!r} in the {src_name} fixture is not registered in "
                                "_KNOWN_BAD_EXPECTED / _KNOWN_GOOD_EXPECTED"
                            )

        if len(res.disabled) != 1 or not res.disabled[0].endswith("test_known_disabled.py"):
            failures.append(f"expected the __test__=False module to be skipped, got {res.disabled}")

        if res.fixtures != 1:
            failures.append(f"expected 1 test_-named fixture to be excluded, got {res.fixtures}")
        if res.test_functions != len(expected_bad) + len(
            expected_good
        ):  # disabled module adds none
            failures.append(
                f"expected {len(expected_bad) + len(expected_good)} test functions, "
                f"got {res.test_functions}"
            )

    for line in failures:
        print(f"self-test FAIL: {line}", file=sys.stderr)
    if failures:
        print(
            f"self-test: {len(failures)} failure(s) -- detector is not trustworthy",
            file=sys.stderr,
        )
        return 3
    print(
        f"self-test: OK -- {len(_KNOWN_BAD_EXPECTED)} known-bad found, "
        f"{len(_KNOWN_GOOD_EXPECTED)} known-good ignored"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="find_vacuous_tests.py",
        description="Find test functions that assert nothing (issue #908).",
        epilog="Exit 0 clean · 1 findings above threshold · 2 usage error · 3 self-test failed",
    )
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        metavar="PATH",
        help=f"file or directory to scan; repeatable (default: {' '.join(DEFAULT_ROOTS)})",
    )
    parser.add_argument(
        "--max-vacuous",
        type=int,
        default=None,
        metavar="N",
        help="exit 0 while the count is <= N; for gating a tree that is not yet clean",
    )
    parser.add_argument("--list", action="store_true", help="print file:line only")
    parser.add_argument("--report", choices=("text", "json"), default="text")
    parser.add_argument(
        "--severity",
        action="append",
        dest="severities",
        choices=("decorative", "unasserted", "swallowed"),
        help="restrict output to these tiers; repeatable",
    )
    parser.add_argument(
        "--follow-imports",
        action="store_true",
        help="credit assertions in helpers defined in other scanned files",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="verify the detector against known-bad and known-good cases, then exit",
    )
    return parser


def render_text(result: ScanResult) -> str:
    lines = [
        "",
        f"vacuous test functions: {result.vacuous} / {result.test_functions}",
    ]
    if result.fixtures:
        lines.append(f"  (excluded {result.fixtures} test_-named pytest fixtures, not collected)")
    if result.test_functions:
        lines.append(f"  ({result.vacuous * 100.0 / result.test_functions:.1f}% of test functions)")
    lines.append("")

    by_sev: dict[str, list[Finding]] = {}
    for f in result.findings:
        by_sev.setdefault(f.severity, []).append(f)

    if not result.findings:
        lines.append("  none found -- every scanned test reaches an assertion")

    for sev in ("unasserted", "decorative", "swallowed"):
        group = by_sev.get(sev)
        if not group:
            continue
        lines.append(f"  {sev} ({len(group)}):")
        for f in group:
            flag = " [swallowed]" if f.swallowed else ""
            lines.append(f"    {f.location}: {f.name}{flag}")
            lines.append(f"        {f.reason} ({f.statements} statement(s))")
        lines.append("")

    if result.unparseable:
        lines.append(f"  unparseable (not counted): {len(result.unparseable)}")
        for p in result.unparseable[:10]:
            lines.append(f"    {p}")
        lines.append("")
    if result.disabled:
        lines.append(f"  __test__ = False (skipped): {len(result.disabled)}")
        for p in result.disabled[:10]:
            lines.append(f"    {p}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    roots = [Path(r) for r in args.roots] if args.roots else [REPO / r for r in DEFAULT_ROOTS]
    for root in roots:
        if not root.exists():
            print(f"error: no such path: {root}", file=sys.stderr)
            return 2

    try:
        result = scan(roots, follow_imports=args.follow_imports)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not result.scanned_files:
        print(
            f"error: no test files found under {', '.join(str(r) for r in roots)}", file=sys.stderr
        )
        return 2

    if args.severities:
        result.findings = [f for f in result.findings if f.severity in args.severities]

    if args.report == "json":
        payload = {
            "vacuous": result.vacuous,
            "test_functions": result.test_functions,
            "scanned_files": result.scanned_files,
            "findings": [
                {
                    "path": f.path,
                    "line": f.lineno,
                    "name": f.name,
                    "severity": f.severity,
                    "reason": f.reason,
                    "statements": f.statements,
                    "swallowed": f.swallowed,
                }
                for f in result.findings
            ],
            "unparseable": result.unparseable,
            "skipped_test_false": result.disabled,
        }
        print(json.dumps(payload, indent=2))
    elif args.list:
        for f in result.findings:
            print(f"{f.location}: {f.name}")
    else:
        print(render_text(result))

    if args.max_vacuous is not None:
        return 0 if result.vacuous <= args.max_vacuous else 1
    return 1 if result.vacuous else 0


if __name__ == "__main__":
    sys.exit(main())
