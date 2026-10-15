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
The first two tiers are ranked by how easily a reviewer dismisses them, not by
how often they occur:

  ``decorative``  The body is a single statement or a docstring. It reads as an
                  unfinished placeholder, so a human skips it. Cheap to fix.
  ``unasserted``  The body is substantial -- it builds fixtures, calls the code
                  under test, maybe branches on the result -- and still asserts
                  nothing. This is the dangerous one. It reads like a test, it
                  sits in a file of real tests, and it survives review because
                  there is nothing obviously missing about it. (AGENTS.md §19.1
                  records a test of exactly this shape passing against a
                  completely broken build.)

The remaining two tiers are ranked by certainty. Both mean the test *does*
reach an assertion, so neither shows up in a naive token scan:

``swallowed``
  The test reaches an assertion, but a construct inside it can carry the
  failure away. The question asked is **what the handler catches**, not what the
  handler is called: ``except AssertionError:`` is the most direct way to
  neutralise a test's own assertion and was invisible to an earlier version of
  this script, while ``except ValueError:`` cannot catch an ``AssertionError``
  and is left alone. Tuples (``except (AssertionError, ValueError)``) and
  ``A | B`` catches are inspected per member, and ``contextlib.suppress(...)``
  is followed even though it parses as ``ast.With`` rather than ``ast.Try``.

``unfalsifiable``
  The test reaches an assertion, and the assertion still cannot fail. Three
  shapes are decided statically, all of them proven rather than guessed:

    tautology            ``assert f(5) == f(5)`` -- both sides identical
    self-computed        ``expected = got; assert got == expected`` -- the
                         expectation is an alias of the value under test, so it
                         moves with it
    decided-by-branch    ``if got is None: assert got == "..."`` -- the guard
                         already fixes the outcome, so the assert is either
                         guaranteed true or guaranteed false

LIMITATIONS -- read before quoting a number
--------------------------------------------
These are **known blind spots**, not hypotheticals. A probe file containing one
instance of each class below was written and the pre-fix detector reported
**0 findings on it**. Do not cite this tool as a measure of "can this test fail"
without carrying these caveats:

  * **Inverted assertions are not detected.** ``assert service.thing() is None``
    -- an assertion that is green only when production is broken -- looks
    identical to a correct one to a syntax-level scan. Catching it needs
    expected-output analysis (what the function is documented to return), which
    is not available statically. Every such test is reported only if it happens
    to assert nothing at all.
  * **Mock *configuration* is not distinguished from mock *behaviour*.**
    ``mock.return_value = X`` followed by ``assert mock.call_args is None`` is
    reported clean, because it asserts. Whether the assertion observes
    production or merely restates what the test itself just set requires
    knowing the mock's role, which this scan does not model.
  * **Cross-module helpers need a flag -- and the flag has a limit.** A test
    that delegates every check to a helper in another module is reported
    vacuous. ``--follow-imports`` extends the search, but only across files the
    scan already collects, i.e. ``test_*.py`` / ``*_test.py``. A helper in a
    *utility* module (``helpers.py``, ``conftest.py``, a production module) is
    still not indexed, and that test is still reported -- verified, not assumed.
    Even when it resolves, lookup is by *name*, so two helpers sharing a name
    are indistinguishable.
  * **A repeated call is not reported, deliberately.**
    ``assert f(5) == f(5)`` reads like the tautology it usually is, but the
    same *syntax* is how this repo tests caching, singletons and freshness --
    ``assert DatabaseManager() is DatabaseManager()``,
    ``assert create_app() is not create_app()``, ``assert Event(...).event_id !=
    Event(...).event_id``. An AST-equality rule for this shape was implemented
    and measured: **20 findings on this repo, every one a sound test, zero
    defects.** Telling the typo from the singleton test needs to know whether
    the callee is pure, which a syntax scan cannot. The tier therefore covers
    ``assert x == x`` on a bare name, where the binding is read twice and there
    is no re-evaluation that could make the two sides differ.
  * **The branch analysis is a bounded proof, not a solver.** It derives facts
    only from single-operator comparisons, ignores ``assert not <compare>``,
    refuses to reason about ``in``/``not in`` (they are not symmetric), and
    goes silent the moment a variable is rebound inside the branch. A test that
    decides its own outcome in a way this cannot express is reported clean.
  * **Self-computed expectations follow plain alias assignment only.**
    ``expected = got`` and chains of it. An expectation built by a call
    (``expected = transform(got)``) is not tracked.
  * **Assertions inside a fixture are not credited to the test.** A test whose
    only check happens in its fixture is reported vacuous. That is usually right
    -- the fixture runs whether or not the test needs it -- but not always.
  * **``__test__ = False`` modules and unparseable files** are reported
    separately under ``--report`` so neither inflates nor hides the count.

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

#: Exception classes that can carry an ``AssertionError`` away from a failing
#: assertion. ``Exception`` and ``BaseException`` belong here because
#: ``AssertionError`` is a subclass of them.
ASSERTION_CATCHERS = frozenset({"AssertionError", "Exception", "BaseException"})

#: The op under which the same comparison fact is false.
_NEGATED_OP = {
    "==": "!=",
    "!=": "==",
    "is": "is not",
    "is not": "is",
    "<": ">=",
    "<=": ">",
    ">": "<=",
    ">=": "<",
    "in": "not in",
    "not in": "in",
}

#: Operators for which ``a op b`` implies ``b op a``. Deliberately excludes
#: ``in``/``not in``: ``x in y`` says nothing about ``y in x``, and treating them
#: as symmetric turns a correct membership assert into "this always fails".
_SYMMETRIC_OPS = frozenset({"==", "!=", "<", ">", "<=", ">=", "is", "is not"})

#: ``ast`` comparison-operator node -> spelling.
_OP_NAMES = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}


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


def _dotted(node: ast.AST) -> str:
    """``"a.b.c"`` for a Name/Attribute chain, ``""`` for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def _caught_names(handler_type: ast.expr | None) -> frozenset[str] | None:
    """Exception classes a handler catches; ``None`` means "cannot tell".

    ``None`` covers a bare ``except:`` (which catches everything) and any shape
    whose class list cannot be read. Callers treat it as catch-all, which is
    the direction that keeps a finding rather than losing one.
    """
    if handler_type is None:
        return None
    if isinstance(handler_type, ast.Tuple):
        names: set[str] = set()
        for elt in handler_type.elts:
            members = _caught_names(elt)
            if members is None:
                return None
            names |= members
        return frozenset(names)
    # `except ValueError | TypeError:` -- a PEP 604 union, which is a BinOp and
    # not a Tuple. Missing this was the same bug one syntax level down.
    if isinstance(handler_type, ast.BinOp) and isinstance(handler_type.op, ast.BitOr):
        left = _caught_names(handler_type.left)
        right = _caught_names(handler_type.right)
        if left is None or right is None:
            return None
        return left | right
    dotted = _dotted(handler_type)
    if not dotted:
        return None
    # `except builtins.AssertionError` must land in the same set as the bare
    # name, so index the final component alongside the full dotted path.
    return frozenset({dotted, dotted.rsplit(".", 1)[-1]})


def _catches_assertion(names: frozenset[str] | None) -> bool:
    """Can a handler catching *names* carry an ``AssertionError`` away?

    ``Exception`` and ``BaseException`` are in the set because
    ``AssertionError`` is a subclass of them -- the reason they swallow an
    assertion is the inheritance, not that the name looks broad.
    """
    if names is None:
        return True
    return bool(names & ASSERTION_CATCHERS)


#: ``with contextlib.suppress(ValueError):`` -- a distinct sentinel rather than
#: ``None``, because "this with-item is not a suppress at all" and "this is a
#: suppress whose caught set cannot be named" are opposite conclusions.
_NOT_A_SUPPRESS = object()


def _suppressed_names(node: ast.expr) -> frozenset[str] | None | object:
    """Classes given to ``contextlib.suppress(...)``.

    Accepts ``contextlib.suppress(E)``, ``ctx.suppress(E)`` and
    ``from contextlib import suppress`` -> ``suppress(E)``. Returns
    ``_NOT_A_SUPPRESS`` when the with-item is something else entirely, an empty
    frozenset for a bare ``suppress()`` (which suppresses nothing, and so is
    correctly *not* a swallow), and ``None`` when the arguments cannot be named.
    """
    if not isinstance(node, ast.Call):
        return _NOT_A_SUPPRESS
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr != "suppress":
            return _NOT_A_SUPPRESS
        # Qualified by `contextlib` or by an alias of it. Anything else --
        # `self.suppress(...)`, `db.suppress(...)` -- is some other object.
        if _dotted(func.value) not in ("", "contextlib"):
            return _NOT_A_SUPPRESS
    elif isinstance(func, ast.Name):
        if func.id != "suppress":
            return _NOT_A_SUPPRESS
    else:
        return _NOT_A_SUPPRESS

    names: set[str] = set()
    for arg in node.args:
        members = _caught_names(arg)
        if members is None:
            return None
        names |= members
    return frozenset(names)


def _has_swallowing_exception_handler(node: ast.AST) -> bool:
    """True if *node* contains a construct that can discard a failed assertion.

    This asks **what the handler catches**, not what the handler is called.
    Matching on the name was a measured defect, not a theoretical one: the old
    matcher tested the caught name against
    ``("Exception", "BaseException", "suppress", "pytest.skip")``, so

        ``except AssertionError:``          MISSED  -- the most direct swallow
        ``except (AssertionError, ValueError):``   MISSED  -- a tuple is one name
        ``contextlib.suppress(AssertionError)``     MISSED  -- an ``ast.With``

    while ``except ValueError:`` would have been reported had its name ever been
    added to the list, despite being unable to catch a failed assertion.

    Two shapes are examined:

    * ``ast.Try`` handlers, with tuple and ``A | B`` catches unpacked. A handler
      swallows when the caught set contains ``AssertionError`` (directly or via
      ``Exception``/``BaseException``, of which it is a subclass), or when it is
      bare and therefore catches everything.
    * ``contextlib.suppress(...)``. It parses as ``ast.With``, so a walk over
      ``ast.Try`` alone can never see it -- which is why the module docstring's
      claim that this function handles ``suppress`` was wrong. ``suppress(AssertionError)``
      is a swallow; ``suppress(ValueError)`` is not.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Try):
            if any(_catches_assertion(_caught_names(h.type)) for h in child.handlers):
                return True
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                names = _suppressed_names(item.context_expr)
                if names is _NOT_A_SUPPRESS:
                    continue
                if _catches_assertion(names):  # type: ignore[arg-type]
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
# Assertions that are present but cannot fail
# ---------------------------------------------------------------------------


def _store_names(node: ast.AST) -> Iterator[str]:
    """Names this statement *binds* (not merely reads)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            yield n.id
        elif isinstance(n, ast.ExceptHandler) and n.name:
            # `except ValueError as e:` binds a plain string, not a Name node.
            yield n.name


def _is_self_tautology(node: ast.Assert) -> str | None:
    """``assert got == got`` on a bare name -- the assertion cannot fail.

    **Only bare ``Name`` operands qualify, and that restriction is the whole
    point.** The obvious generalisation -- "both sides are the same expression,
    so compare their ASTs" -- was tried first and produced **20 findings on this
    repo, every one of them a sound test and none of them a defect**:

        assert DatabaseManager.get_engine() is DatabaseManager.get_engine()  # caching
        assert DatabaseManager() is DatabaseManager()                        # singleton
        assert Event(topic="t", key="1").event_id != Event(...).event_id    # fresh uuid
        assert create_app() is not create_app()                              # not cached

    A repeated *call* is two evaluations, and testing that they are equal (it is
    cached), or that they differ (it is fresh), is exactly what those tests are
    for. Distinguishing that from a typo like ``assert broken.compute(5) ==
    broken.compute(5)`` needs to know whether the callee is pure, which is not
    available to a syntax scan. So the call case is left alone on purpose; see
    LIMITATIONS.
    """
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    left = test.left
    if not isinstance(left, ast.Name):
        return None
    for comparator in test.comparators:
        if isinstance(comparator, ast.Name) and comparator.id == left.id:
            return "compares a name with itself, so it cannot fail"
    return None


def _self_computed_assertion(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """``expected = got; assert got == expected`` -- the expectation moves with got.

    Only plain alias assignment is followed (``expected = got`` and chains of
    it). A name rebound anywhere drops the chain, so the realistic variant

        got = compute(); expected = got; got = other(); assert got == expected

    is correctly left alone -- ``expected`` still holds the *first* value.
    """
    aliases: dict[str, str] = {}

    def root(name: str) -> str:
        seen: set[str] = set()
        while name in aliases and name not in seen:
            seen.add(name)
            name = aliases[name]
        return name

    def forget(name: str) -> None:
        aliases.pop(name, None)
        for key in [k for k, v in aliases.items() if v == name]:
            aliases.pop(key)

    for stmt in func.body:
        for name in _store_names(stmt):
            forget(name)
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
            if isinstance(target, ast.Name) and isinstance(value, ast.Name):
                aliases[target.id] = root(value.id)
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Assert):
                continue
            test = node.test
            if not isinstance(test, ast.Compare) or len(test.ops) != 1:
                continue
            if not isinstance(test.left, ast.Name):
                continue
            if any(
                isinstance(c, ast.Name) and root(c.id) == root(test.left.id)
                for c in test.comparators
            ):
                return (
                    "expectation is derived from the value under test, " "so the assert cannot fail"
                )
    return None


def _guard_facts(expr: ast.expr) -> list[tuple[str, ast.expr, ast.expr]]:
    """Comparisons guaranteed true whenever *expr* is true.

    One-directional on purpose: it derives only facts that hold on **every**
    path through the expression, so an unrecognised shape yields an empty list
    and the check stays silent rather than guessing. ``a or b`` yields nothing
    -- neither disjunct's facts hold unconditionally -- and ``a and b`` yields
    the intersection, which does.
    """
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
        op = _OP_NAMES.get(type(expr.ops[0]))
        if op is None:
            return []
        return [(op, expr.left, expr.comparators[0])]
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return _negate_facts(_guard_facts(expr.operand))
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
        sets = [_guard_facts(v) for v in expr.values]
        return [f for f in sets[0] if all(f in s for s in sets[1:])] if sets else []
    return []


def _negate_facts(
    facts: list[tuple[str, ast.expr, ast.expr]],
) -> list[tuple[str, ast.expr, ast.expr]]:
    return [(neg, lhs, rhs) for op, lhs, rhs in facts if (neg := _NEGATED_OP.get(op))]


def _pinned_constants(
    facts: list[tuple[str, ast.expr, ast.expr]],
) -> dict[str, tuple[object, str]]:
    """Variables the guard fixes to a constant, as ``dump -> (value, op)``."""
    pinned: dict[str, tuple[object, str]] = {}
    for op, left, right in facts:
        if op not in ("is", "is not", "==", "!="):
            continue
        if isinstance(right, ast.Constant) and not isinstance(left, ast.Constant):
            pinned.setdefault(ast.dump(left), (right.value, op))
        elif isinstance(left, ast.Constant) and not isinstance(right, ast.Constant):
            pinned.setdefault(ast.dump(right), (left.value, op))
    return pinned


def _constants_equal(left: object, right: object) -> bool:
    """Constant equality, using Python's own ``==`` so we never over-claim.

    This function feeds both "always true" and "always false" verdicts, so a
    false "not equal" would be a false-positive finding. ``True == 1`` being
    ``True`` in Python keeps us on the safe side of that.
    """
    try:
        return bool(left == right)
    except Exception:  # pragma: no cover - ast.Constant holds only primitives
        return True


def _branch_assertion_reason(
    node: ast.Assert,
    facts: list[tuple[str, ast.expr, ast.expr]],
    pinned: dict[str, tuple[object, str]],
    rebound: set[str],
) -> str | None:
    """Reason string when the guard has already decided this assertion's outcome."""
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None  # `assert False`, a bare `assert x`, ...
    op = _OP_NAMES.get(type(test.ops[0]))
    if op is None:
        return None
    left, right = test.left, test.comparators[0]
    if any(isinstance(n, ast.Name) and n.id in rebound for n in (left, right)):
        return None  # the branch reassigns it, so the guard no longer pins it

    lk, rk = ast.dump(left), ast.dump(right)
    holds = {(o, ast.dump(lhs), ast.dump(rhs)) for o, lhs, rhs in facts}
    # Restated by the guard: true whatever the code under test does. Swapped
    # operands count too -- `if a == b: assert b == a` says the same thing --
    # which is why `_SYMMETRIC_OPS` gates both directions.
    if (op, lk, rk) in holds or (op in _SYMMETRIC_OPS and (op, rk, lk) in holds):
        return "restates the branch condition, so it cannot fail"
    negated = _NEGATED_OP.get(op)
    if negated is not None and negated in _SYMMETRIC_OPS:
        if (negated, lk, rk) in holds or (negated, rk, lk) in holds:
            return "contradicts the branch condition, so it always fails"

    # The guard pins one operand to a constant; the assert compares that same
    # operand against another constant. `if got is None: assert got == "x"`
    # lands here -- `None == "x"` is false on every run.
    for key, const_node in ((lk, right), (rk, left)):
        pin = pinned.get(key)
        if pin is None or not isinstance(const_node, ast.Constant):
            continue
        value, _pin_op = pin
        same = _constants_equal(value, const_node.value)
        if op in ("==", "is"):
            if not same:
                return "contradicts the branch condition, so it always fails"
        elif op in ("!=", "is not") and same:
            return "restates the branch condition, so it cannot fail"
    return None


def _decided_by_branch(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """``if got is None: assert got == "..."`` -- the guard decides the assert.

    Walks ``ast.If`` rather than statements, so a guard nested anywhere in the
    body counts. The ``else`` branch is handled by negating the guard's facts;
    an ``elif`` is skipped here and picked up on its own as a nested ``If``.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        facts = _guard_facts(node.test)
        if not facts:
            continue
        branches = [(facts, node.body)]
        if node.orelse and not any(isinstance(n, ast.If) for n in node.orelse):
            branches.append((_negate_facts(facts), node.orelse))
        for guard_facts, body in branches:
            pinned = _pinned_constants(guard_facts)
            rebound: set[str] = set()
            for stmt in body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Assert):
                        reason = _branch_assertion_reason(sub, guard_facts, pinned, rebound)
                        if reason:
                            return reason
                rebound.update(_store_names(stmt))
    return None


def _unfalsifiable_reason(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Why *func*'s assertions cannot fail, if that is the problem.

    Each check decides its class by proof, not by pattern-matching on names:
    an assertion it cannot show to be constant is left alone. The three classes
    it cannot decide -- an inverted assertion, a mock *configuration* assert,
    and a cross-module helper -- are listed in the module docstring's
    LIMITATIONS rather than approximated here.
    """
    for stmt in func.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assert):
                reason = _is_self_tautology(node)
                if reason:
                    return reason
    return _self_computed_assertion(func) or _decided_by_branch(func)


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
            # Two different reasons a test can reach an assertion and still not
            # be able to fail. `unfalsifiable` is ranked first because it is
            # decided by proof -- the assertion's outcome is fixed whatever the
            # code under test does -- whereas `swallowed` means the handler
            # *can* discard the failure, which depends on what actually raises.
            unfalsifiable = _unfalsifiable_reason(func)
            swallowed = _has_swallowing_exception_handler(func)
            if unfalsifiable:
                result.findings.append(
                    Finding(
                        path=rel,
                        lineno=func.lineno,
                        name=func.name,
                        severity="unfalsifiable",
                        reason=unfalsifiable,
                        swallowed=swallowed,
                        statements=len(func.body),
                    )
                )
            elif swallowed:
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

def test_narrow_handler_does_not_swallow_an_assertion():
    # The negative half of the swallowed fix. `ValueError` cannot catch the
    # AssertionError an assert raises, so this handler cannot discard the
    # failure and the test is fine. Matching on handler *names* could not tell
    # this apart from `except AssertionError:` -- it needs the caught types.
    try:
        assert service.thing() is not None
    except ValueError:
        pass

def test_suppress_of_an_unrelated_error_is_not_a_swallow():
    # `contextlib.suppress(ValueError)` parses as ast.With and suppresses
    # nothing an assert can raise. Must NOT be reported, or the fix over-corrects
    # into flagging every legitimate suppress.
    import contextlib
    with contextlib.suppress(ValueError):
        assert compute(5) == 6

def test_narrow_tuple_handler_does_not_swallow_either(service):
    # The negative half of the tuple fix, and the case that kills the mutant
    # which removes tuple unpacking: with unpacking gone, a tuple falls through
    # to "cannot tell", which `_catches_assertion` treats as catch-all, so this
    # would be flagged as a swallow it is not.
    try:
        assert service.thing() is not None
    except (ValueError, KeyError):
        pass


def test_assert_comparing_two_different_things(service):
    # Not a tautology: the two sides are different expressions.
    assert service.compute(5) == 6

def test_assert_with_an_independently_computed_expectation(service):
    # Not self-computed: `expected` is built by a call, not aliased to `got`.
    got = service.thing()
    expected = service.other()
    assert got == expected

def test_recomputed_value_is_not_an_alias(service):
    # The realistic variant of the self-computed shape. `expected` still holds
    # the FIRST result, so this assert can genuinely fail and must stay clean.
    got = service.thing()
    expected = got
    got = service.other()
    assert got == expected

def test_branch_that_does_not_decide_the_assertion(service):
    # The guard does not pin `got` to a constant, and `got` is rebound inside
    # the branch anyway. The assert is reachable and can fail.
    got = service.thing()
    if got is None:
        got = service.fallback()
    assert got is not None

def test_branch_that_rebinds_then_asserts(service):
    # `if result is None: result = ""` rebinds before the assert, so the guard no
    # longer pins `result` to None and `result == ""` is a real check.
    #
    # The rebound value and the asserted constant must both be Constants, or this
    # case stops testing the rebind tracking: with `result = []` the comparison
    # operand is a Dict, the constant-pinning path skips it anyway, and a
    # mutant that deletes the rebind check survives. That was found by running
    # the battery, not by reading this.
    result = service.thing()
    if result is None:
        result = ""
        assert result == ""

def test_alternating_branches_keep_the_assert_alive(service):
    got = service.thing()
    if got is None:
        got = service.fallback()
    else:
        got = service.other()
    assert got is not None
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


def test_asserts_but_an_assertion_error_handler_can_swallow_it():
    # The most direct swallow there is: catch the exact exception an assert
    # raises. Matching handler *names* against ("Exception", "BaseException",
    # ...) cannot see it, because "AssertionError" is not in that list.
    try:
        assert service.thing() is not None
    except AssertionError:
        pass


def test_asserts_but_a_tuple_handler_can_swallow_it():
    # A tuple catch is one ast.Tuple node holding two classes. The old matcher
    # read it as a single name and matched neither member.
    try:
        assert service.thing() is not None
    except (AssertionError, ValueError):
        pass


def test_asserts_but_contextlib_suppress_can_swallow_it():
    # contextlib.suppress parses as ast.With, so a walk over ast.Try alone
    # never sees it -- which is why the module docstring used to claim it was
    # handled when it was not. Same hazard, different syntax.
    import contextlib
    with contextlib.suppress(AssertionError):
        assert service.thing() is not None


def test_asserts_the_same_expression_against_itself(broken):
    # Tautology on a bare name: one binding read twice, no re-evaluation, so
    # the comparison is decided before either side is fetched.
    got = broken.compute(5)
    assert got == got


def test_two_calls_must_be_cached(service):
    # NOT a tautology, and the detector must keep quiet. `DatabaseManager() is
    # DatabaseManager()` is a real singleton test -- it fails if the cache
    # breaks. An AST-equality rule flagged 20 such tests in this repo and found
    # no defects; `_is_self_tautology` documents that measurement.
    assert service.build() is service.build()


def test_two_calls_must_be_fresh(service):
    # The mirror image: asserting two evaluations *differ* is how freshness and
    # uniqueness are tested. Also must stay clean.
    assert service.next_id() != service.next_id()


def test_expectation_is_aliased_from_the_value_under_test(service):
    # The assertion holds whatever `got` holds, so it can never fail.
    got = service.thing()
    expected = got
    assert got == expected


def test_assert_contradicts_the_branch_that_guards_it(service):
    # Under the guard `got` *is* None, so `None == "must not be None"` is false
    # on every run: the branch decides the outcome before the assert is read.
    got = service.thing()
    if got is None:
        assert got == "must not be None"


def test_assert_contradicts_the_branch_with_swapped_operands(service):
    # The same contradiction with the operands written the other way round, so
    # it is only reachable through the symmetry rule. Neither operand is a
    # constant, so the constant-pinning path cannot cover this one.
    got = service.thing()
    limit = service.limit()
    if got == limit:
        assert limit != got


def test_assert_restates_the_branch_with_swapped_operands(service):
    # `if a == b: assert b == a` says the same thing the guard already said.
    got = service.thing()
    limit = service.limit()
    if got == limit:
        assert limit == got
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
    # The three shapes the old handler-name matcher provably missed.
    "test_asserts_but_an_assertion_error_handler_can_swallow_it",
    "test_asserts_but_a_tuple_handler_can_swallow_it",
    "test_asserts_but_contextlib_suppress_can_swallow_it",
    # The three assertion shapes that are present but cannot fail.
    "test_asserts_the_same_expression_against_itself",
    "test_expectation_is_aliased_from_the_value_under_test",
    "test_assert_contradicts_the_branch_that_guards_it",
    "test_assert_contradicts_the_branch_with_swapped_operands",
    "test_assert_restates_the_branch_with_swapped_operands",
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
    # Negative halves. Each is the near-miss of a new check, and each is the
    # case that proves the check is a proof rather than a pattern match: a
    # detector that flags all of these is worse than one that flags none.
    "test_narrow_handler_does_not_swallow_an_assertion",
    "test_narrow_tuple_handler_does_not_swallow_either",
    "test_suppress_of_an_unrelated_error_is_not_a_swallow",
    "test_assert_comparing_two_different_things",
    "test_assert_with_an_independently_computed_expectation",
    "test_recomputed_value_is_not_an_alias",
    "test_branch_that_does_not_decide_the_assertion",
    "test_branch_that_rebinds_then_asserts",
    "test_alternating_branches_keep_the_assert_alive",
    # A repeated call is a caching/freshness test, not a tautology. These two
    # pin the narrowing of `_is_self_tautology` to bare names; an AST-equality
    # rule flagged 20 real tests in this repo and found no defects.
    "test_two_calls_must_be_cached",
    "test_two_calls_must_be_fresh",
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

        # Each of the swallowed shapes the old handler-name matcher missed. Named
        # individually rather than looped over a constant, because the failure
        # this guards against is precisely the one where a new case is added to
        # the fixture and only the old set is still checked.
        for name in (
            "test_asserts_but_a_truly_bare_except_can_swallow_it",
            "test_asserts_but_an_assertion_error_handler_can_swallow_it",
            "test_asserts_but_a_tuple_handler_can_swallow_it",
            "test_asserts_but_contextlib_suppress_can_swallow_it",
        ):
            if by_name.get(name) != "swallowed":
                failures.append(f"{name} should be tagged 'swallowed', got {by_name.get(name)!r}")

        # The three assertion shapes that are present but cannot fail. These are
        # the checks the pre-fix version had no tier for at all.
        for name in (
            "test_asserts_the_same_expression_against_itself",
            "test_expectation_is_aliased_from_the_value_under_test",
            "test_assert_contradicts_the_branch_that_guards_it",
        ):
            if by_name.get(name) != "unfalsifiable":
                failures.append(
                    f"{name} should be tagged 'unfalsifiable', got {by_name.get(name)!r}"
                )

        # The tier alone is not enough to isolate these three. `assert got == got`
        # is matched by BOTH the tautology check and the self-computed check --
        # a bare name resolves to itself -- so disabling the tautology check
        # still leaves the test reported, and a battery run on severity alone
        # calls that mutant killed when it is not. Asserting the *reason* is what
        # makes each case name the check it was written for; this was found by
        # a mutant that survived, not by reading the code.
        by_reason = {f.name: f.reason for f in res.findings}
        for name, expected_fragment in (
            ("test_asserts_the_same_expression_against_itself", "with itself"),
            ("test_expectation_is_aliased_from_the_value_under_test", "derived from the value"),
            ("test_assert_contradicts_the_branch_that_guards_it", "contradicts the branch"),
        ):
            reason = by_reason.get(name, "")
            if expected_fragment not in reason:
                failures.append(
                    f"{name} should be reported for the reason containing "
                    f"{expected_fragment!r}, got {reason!r} -- if a neighbouring check "
                    "is shadowing it, this case is not testing what it claims"
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
        choices=("decorative", "unasserted", "swallowed", "unfalsifiable"),
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

    for sev in ("unfalsifiable", "unasserted", "decorative", "swallowed"):
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
