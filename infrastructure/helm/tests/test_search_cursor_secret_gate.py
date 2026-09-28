#!/usr/bin/env python3
"""Chart gate test: SEARCH_CURSOR_SECRET must fail loudly at `helm template`.

The claim under test is narrow and falsifiable: a production render with no
search-cursor secret key name must abort with a chart-level error, instead of
emitting a Deployment whose `secretKeyRef.key` is null and letting the pod
crash-loop on search-service's own startup validator.

Everything here is proven by running `helm template`. Reading `_helpers.tpl`
and seeing a `fail` in it proves nothing: a define that nothing includes is
inert, and a check that only ever runs for the wrong values is decoration.
So every assertion below shells out and inspects the exit status and stderr.

The chart under test can be redirected with WILDFRAME_HELM_CHART, which is how
the guards in this file were mutation-verified without editing the real chart.
It must point at a directory that also has a sibling `values-production.yaml`.

Usage:
    python3 tests/test_search_cursor_secret_gate.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import yaml

from test_production_external import PROD, assert_fail, assert_success, helm_template

SCRATCH = Path(__file__).resolve().parents[3] / "tem" / "search_cursor_gate"

# The exact string the gate must produce. Asserted, not just matched-for-
# non-emptiness, so that removing this check and leaving some other
# production check in place cannot be mistaken for it still working: with the
# key empty, the searchCursorSecretKey check is the only one that can fire.
GATE_MESSAGE = "production requires secrets.searchCursorSecretKey set"


def chart() -> Path:
    """Chart directory under test; overridable so guards can be mutated."""
    return Path(os.environ.get("WILDFRAME_HELM_CHART", PROD.parent / "wildframe"))


def prod_values() -> Path:
    override = os.environ.get("WILDFRAME_HELM_PROD_VALUES")
    return Path(override) if override else PROD


def render(*, chart_dir: Path, values: Path | None, namespace: str, sets=()):
    """helm template with a redirected chart dir.

    Mirrors test_production_external.helm_template; that helper hardcodes the
    in-repo chart path, which is the one thing mutation verification has to
    change.
    """
    cmd = ["helm", "template", "wildframe", str(chart_dir), "--namespace", namespace]
    if values is not None:
        cmd += ["-f", str(values)]
    for item in sets:
        cmd += ["--set-string", item]
    return subprocess.run(cmd, capture_output=True, text=True)


def search_cursor_env_entries(stdout: str) -> list[tuple[str, object]]:
    """(deployment name, secretKeyRef.key) for every SEARCH_CURSOR_SECRET env var.

    Parses the render instead of grepping it, so a `key:` that YAML reads as
    null is reported as None rather than hidden behind a substring match.
    """
    entries = []
    for doc in yaml.safe_load_all(stdout):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"].get("containers", []):
            for env in container.get("env", []):
                if env.get("name") != "SEARCH_CURSOR_SECRET":
                    continue
                ref = (env.get("valueFrom") or {}).get("secretKeyRef") or {}
                entries.append((doc["metadata"]["name"], ref.get("key")))
    return entries


def gate_message(res) -> str:
    """The first line of the abort message, stripped of helm's location prefix."""
    first = res.stderr.strip().splitlines()[0] if res.stderr.strip() else ""
    return first.split("): ", 1)[-1]


# --- the gate must fire, and must fire for the right reason ----------------


def test_production_render_with_key_set_succeeds():
    """Baseline: the gate does not fire on the shipped production values.

    Without this, a gate that fires unconditionally would satisfy every
    "must fail" test below while making production undeployable.
    """
    res = render(chart_dir=chart(), values=prod_values(), namespace="wildframe-production")
    assert_success(res, "production with the search cursor key set")


def test_production_fails_loudly_when_key_is_empty():
    """The headline claim: render aborts, and no manifest comes out.

    Asserts the *specific* message, not merely a non-zero exit. Every other
    production check in this helper (postgres, redis, cidrs) passes on these
    values, so a wrong-reason failure would be indistinguishable from the real
    one unless the text is checked.
    """
    res = render(
        chart_dir=chart(),
        values=prod_values(),
        namespace="wildframe-production",
        sets=("secrets.searchCursorSecretKey=",),
    )
    assert_fail(res, "production with an empty search cursor secret key")
    assert GATE_MESSAGE in gate_message(res), (
        f"expected the searchCursorSecretKey gate to be the reason, got: {res.stderr.strip()}"
    )
    assert res.stdout == "", "a failing render must not emit partial manifests"


def test_production_fails_loudly_when_key_is_explicitly_null():
    """`--set k=null` removes the key from the merged values.

    Different input path from the empty string, and the one a templating
    mistake or a values-file edit would actually produce.
    """
    cmd = [
        "helm", "template", "wildframe", str(chart()),
        "--namespace", "wildframe-production",
        "-f", str(prod_values()),
        "--set", "secrets.searchCursorSecretKey=null",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert_fail(res, "production with a null search cursor secret key")
    assert GATE_MESSAGE in gate_message(res), res.stderr.strip()


def test_production_fails_loudly_when_key_is_absent_entirely():
    """No default and no values-file entry: the value is missing, not blank.

    This is the case the empty-string test cannot reach, because the shipped
    chart *does* ship a default. Rendered against a copy of the chart with that
    default deleted and the production entry deleted, so the value is genuinely
    absent from the merged result.
    """
    SCRATCH.mkdir(parents=True, exist_ok=True)
    copy = SCRATCH / "chart-no-default"
    values = SCRATCH / "values-production-no-key.yaml"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(chart(), copy)

    default_line = "  searchCursorSecretKey: SEARCH_CURSOR_SECRET\n"
    chart_values = (copy / "values.yaml").read_text()
    assert default_line in chart_values, (
        "values.yaml no longer ships secrets.searchCursorSecretKey; "
        "this test needs to delete that default to reach the absent case"
    )
    (copy / "values.yaml").write_text(chart_values.replace(default_line, ""))

    prod_text = prod_values().read_text()
    assert default_line in prod_text, (
        "values-production.yaml no longer sets secrets.searchCursorSecretKey; "
        "this test deletes that entry to reach the absent case"
    )
    values.write_text(prod_text.replace(default_line, ""))

    try:
        res = render(chart_dir=copy, values=values, namespace="wildframe-production")
        assert_fail(res, "production with the search cursor key absent entirely")
        assert GATE_MESSAGE in gate_message(res), res.stderr.strip()
    finally:
        shutil.rmtree(copy, ignore_errors=True)
        values.unlink(missing_ok=True)


def test_production_wiring_uses_the_configured_key_name():
    """The gate is only meaningful if the rendered ref actually uses the value.

    Guards the deployment wiring independently of the gate: a chart that fails
    loudly on an empty key but silently drops the env var, or points it at a
    hardcoded name, would pass every test above.
    """
    res = render(chart_dir=chart(), values=prod_values(), namespace="wildframe-production")
    assert_success(res, "production wiring")
    entries = search_cursor_env_entries(res.stdout)
    assert entries == [("search-service", "SEARCH_CURSOR_SECRET")], (
        f"expected search-service alone to read SEARCH_CURSOR_SECRET from the "
        f"configured key, got {entries}"
    )

    overridden = render(
        chart_dir=chart(),
        values=prod_values(),
        namespace="wildframe-production",
        sets=("secrets.searchCursorSecretKey=CURSOR_KEY_V2",),
    )
    assert_success(overridden, "production with a renamed key")
    assert search_cursor_env_entries(overridden.stdout) == [
        ("search-service", "CURSOR_KEY_V2")
    ], "the rendered secretKeyRef must follow secrets.searchCursorSecretKey"


# --- the gate must not fire outside production -----------------------------


def test_default_render_does_not_invoke_the_gate():
    """Default values carry an in-cluster postgres/redis; the gate must stay quiet.

    Asserting only the *message* is absent rather than "the render succeeded"
    keeps this meaningful if some unrelated production check is tightened later.
    """
    res = render(chart_dir=chart(), values=None, namespace="wildframe")
    assert_success(res, "default values")
    assert GATE_MESSAGE not in res.stderr
    assert search_cursor_env_entries(res.stdout) == [("search-service", "SEARCH_CURSOR_SECRET")]


def test_staging_render_does_not_invoke_the_gate():
    """Staging is a separate environment with its own secret contract."""
    res = render(
        chart_dir=chart(),
        values=prod_values().parent / "values-staging.yaml",
        namespace="wildframe-staging",
    )
    assert_success(res, "staging values")
    assert GATE_MESSAGE not in res.stderr


def test_staging_does_not_require_a_cursor_key():
    """Negative-control: the same input that fails in production is fine in staging.

    This is the assertion that would catch the gate's scope being widened, e.g.
    a stray `required` or a check moved outside the `$isProd` block.
    """
    staging_values = prod_values().parent / "values-staging.yaml"
    values = SCRATCH / "values-staging-no-cursor-key.yaml"
    SCRATCH.mkdir(parents=True, exist_ok=True)
    assert "searchCursorSecretKey" not in staging_values.read_text(), (
        "values-staging.yaml now sets secrets.searchCursorSecretKey; this test "
        "overrides it with an empty key to prove staging is not gated"
    )
    values.write_text(
        staging_values.read_text().rstrip("\n") + "\nsecrets:\n  searchCursorSecretKey: ''\n"
    )

    try:
        res = render(chart_dir=chart(), values=values, namespace="wildframe-staging")
        assert_success(res, "staging with an empty cursor key")
        assert GATE_MESSAGE not in res.stderr
    finally:
        values.unlink(missing_ok=True)


if __name__ == "__main__":
    checks = [
        test_production_render_with_key_set_succeeds,
        test_production_fails_loudly_when_key_is_empty,
        test_production_fails_loudly_when_key_is_explicitly_null,
        test_production_fails_loudly_when_key_is_absent_entirely,
        test_production_wiring_uses_the_configured_key_name,
        test_default_render_does_not_invoke_the_gate,
        test_staging_render_does_not_invoke_the_gate,
        test_staging_does_not_require_a_cursor_key,
    ]
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print("ALL PASS")
