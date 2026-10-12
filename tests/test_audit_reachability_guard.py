"""Tests for scripts/assert-dev-audit-unreachable.sh.

The frontend SCA gate runs `npm audit --omit=dev`, so dev-only advisories with no
upstream patch would otherwise be invisible to it. This guard is what makes that
safe: it fails when a package assumed dev-only becomes reachable from production.

The load-bearing test here is `test_detects_a_production_package`. A guard that
only ever passes is indistinguishable from no guard at all, so we point it at a
package that genuinely is in the production graph and require a non-zero exit.
That is the assertion that would catch a future change making `braces` reachable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "assert-dev-audit-unreachable.sh"

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "package-lock.json").exists(),
    reason="workspace lockfile absent; npm cannot resolve the dependency graph",
)


def _run(*packages: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *packages],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.exists(), f"guard script missing: {SCRIPT}"
    assert SCRIPT.stat().st_mode & 0o111, "guard script is not executable"


def test_default_package_is_not_reachable_from_production() -> None:
    """`braces` must stay dev-only; this is the assumption the gate relies on."""
    result = _run()
    assert result.returncode == 0, (
        f"default guard run failed (rc={result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "braces" in result.stdout


def test_detects_a_production_package() -> None:
    """The load-bearing case: pointed at a real production dep, it must fail.

    Without this, a guard that silently stopped checking anything would still
    pass every other test in this file.
    """
    result = _run("next")
    assert result.returncode == 1, (
        "guard failed to flag a package that IS in the production graph; "
        "the reachability check is not load-bearing\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "next" in result.stderr


def test_absent_package_passes() -> None:
    """A package that does not exist must not be reported as reachable."""
    result = _run("this-package-does-not-exist-wildframe")
    assert result.returncode == 0, result.stderr


def test_error_output_is_machine_readable() -> None:
    """CI must be able to surface the failure as an annotation, not just text."""
    result = _run("next")
    assert "::error" in result.stderr, "no GitHub error annotation emitted"


def test_production_dependency_audit_is_clean() -> None:
    """The gate itself must have nothing to report for production deps."""
    audit = subprocess.run(
        ["npm", "audit", "--omit=dev", "--audit-level=high"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert audit.returncode == 0, (
        "npm audit --omit=dev --audit-level=high failed; the SCA gate would be red\n"
        f"{audit.stdout}\n{audit.stderr}"
    )
