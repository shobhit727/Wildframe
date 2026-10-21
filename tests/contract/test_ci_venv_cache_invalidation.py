"""Contract check: the CI virtualenv cache must know about path dependencies.

Every service depends on `packages/sdk/*` by path. Poetry installs a path
dependency keyed on that package's **declared version**, so when an SDK module
changes shape without a version bump, a restored virtualenv silently keeps the
old copy of the SDK.

That happened here: `wire_observability` gained a `register_metrics`
keyword-only argument while the package stayed at `1.0.0`, so
`wire_observability(register_metrics=False)` — which all fifteen services now
call — raised `TypeError` on import against a cached venv. The service's own
`poetry.lock` and `pyproject.toml` were untouched, so a cache key built from
those files alone restored the poisoned venv, and the failure appeared only in
CI.

The fix is to hash the SDK into the key, plus a one-time `venv-v4` -> `venv-v5`
bump so existing poisoned entries are not resurrected by `restore-keys`. This
test exists so nobody quietly reverts the hash and reintroduces it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci-cd.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_service_venv_cache_key_hashes_the_sdk() -> None:
    """A cache key built only from the service's own files cannot see an SDK change."""
    text = _workflow_text()
    key_lines = [
        line
        for line in text.splitlines()
        if line.strip().startswith("key: venv-")
    ]
    assert key_lines, "no service virtualenv cache key found in the workflow"

    for line in key_lines:
        assert "packages/sdk" in line, (
            "the service venv cache key does not hash packages/sdk, so an SDK "
            "change that keeps the same package version restores a stale SDK "
            f"into the venv: {line.strip()[:120]}"
        )


def test_restore_keys_use_the_same_prefix_as_the_key() -> None:
    """A `restore-keys` on the old prefix resurrects the poisoned cache.

    `restore-keys` is a prefix match, so bumping only the `key:` line would let
    every old entry be restored under a new name and the fix would be
    cosmetic.
    """
    text = _workflow_text()
    key = next(
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("key: venv-")
    )
    prefix = key.split("${{")[0].strip()

    restore = re.findall(r"^\s*venv-\d+-.*$", text, re.MULTILINE)
    stale = [
        r.strip()
        for r in restore
        if r.strip() != prefix and r.strip().startswith("venv-")
    ]
    assert not stale, (
        f"restore-keys reference prefixes other than the current key {prefix!r}: "
        f"{stale}"
    )


def test_workflow_still_parses_and_pins_actions() -> None:
    """Sanity: the edit did not break YAML, and action pinning is intact."""
    data = yaml.safe_load(_workflow_text())
    assert "jobs" in data, "workflow no longer parses into jobs"
    # The cache action must remain pinned to a full SHA; an unpinned action is a
    # supply-chain regression and this file is in the security-scan path.
    for match in re.finditer(r"uses:\s*([^\s#]+)@([^\s#]+)", _workflow_text()):
        ref = match.group(2).strip()
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"action {match.group(1)} is not pinned to a 40-char SHA: {ref!r}"
        )
