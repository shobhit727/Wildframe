#!/usr/bin/env python3
"""Chart test for #1133: every service gets METRICS_TOKEN from a Secret.

Each service's /metrics guard rejects every request with 401 when it runs with
ENVIRONMENT=production and an empty METRICS_TOKEN. The chart defaults every
service to ENVIRONMENT=production, so if the chart does not inject the token,
no cluster built from it has any service metrics.

This file checks two things, both by running `helm template`:
  (a) under staging and production values, every rendered service Deployment
      has a METRICS_TOKEN env var sourced from a secretKeyRef, never a literal
      value;
  (b) a production render with no metrics token key configured fails at
      render time. It does not emit Deployments that 401 every scrape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from test_production_external import CHART, PROD

STAGING = Path(__file__).resolve().parent.parent / "values-staging.yaml"

# Asserted exactly, so another production check firing cannot pass for this one.
GATE_MESSAGE = "production requires secrets.metricsTokenKey set"


def render(values: Path | None, namespace: str, sets=()):
    cmd = ["helm", "template", "wildframe", str(CHART), "--namespace", namespace]
    if values is not None:
        cmd += ["-f", str(values)]
    for item in sets:
        cmd += ["--set", item] if item.endswith("=null") else ["--set-string", item]
    return subprocess.run(cmd, capture_output=True, text=True)


def deployments(stdout: str) -> list[dict]:
    return [
        doc
        for doc in yaml.safe_load_all(stdout)
        if isinstance(doc, dict) and doc.get("kind") == "Deployment"
    ]


def metrics_token_refs(stdout: str) -> dict[str, list[dict]]:
    """Deployment name -> list of METRICS_TOKEN env entries across its containers."""
    out: dict[str, list[dict]] = {}
    for dep in deployments(stdout):
        name = dep["metadata"]["name"]
        entries = []
        for container in dep["spec"]["template"]["spec"]["containers"]:
            entries += [e for e in container.get("env") or [] if e.get("name") == "METRICS_TOKEN"]
        out[name] = entries
    return out


def assert_every_deployment_reads_token_from_secret(stdout: str, key: str = "METRICS_TOKEN"):
    refs = metrics_token_refs(stdout)
    assert len(refs) >= 15, f"expected every service Deployment, got {sorted(refs)}"
    missing = sorted(name for name, entries in refs.items() if not entries)
    assert not missing, f"Deployments without METRICS_TOKEN: {missing}"
    for name, entries in refs.items():
        assert len(entries) == 1, f"{name}: METRICS_TOKEN set {len(entries)} times"
        entry = entries[0]
        assert "value" not in entry, f"{name}: METRICS_TOKEN must not be a literal value"
        ref = (entry.get("valueFrom") or {}).get("secretKeyRef") or {}
        assert ref.get("name") == "wildframe-runtime", f"{name}: secretKeyRef.name {ref.get('name')!r}"
        assert ref.get("key") == key, f"{name}: secretKeyRef.key {ref.get('key')!r}"


@pytest.mark.parametrize(
    ("values", "namespace"),
    [(STAGING, "wildframe-staging"), (PROD, "wildframe-production")],
    ids=["staging", "production"],
)
def test_every_deployment_gets_metrics_token_from_secret(values, namespace):
    res = render(values, namespace)
    assert res.returncode == 0, res.stderr
    assert_every_deployment_reads_token_from_secret(res.stdout)


def test_production_uses_the_configured_key_name():
    res = render(PROD, "wildframe-production", ["secrets.metricsTokenKey=PROM_SCRAPE_TOKEN"])
    assert res.returncode == 0, res.stderr
    assert_every_deployment_reads_token_from_secret(res.stdout, key="PROM_SCRAPE_TOKEN")


@pytest.mark.parametrize(
    "override",
    ["secrets.metricsTokenKey=", "secrets.metricsTokenKey=   ", "secrets.metricsTokenKey=null"],
    ids=["empty", "whitespace", "null"],
)
@pytest.mark.parametrize("namespace", ["wildframe-production", "renamed-prod"])
def test_production_without_metrics_token_key_fails_to_render(override, namespace):
    # The gate is keyed on .Values.environment, so it also holds when the
    # production values are rendered into a namespace with a different name.
    res = render(PROD, namespace, [override])
    assert res.returncode != 0, "production render without a metrics token key should fail"
    assert GATE_MESSAGE in res.stderr, res.stderr


def test_staging_without_metrics_token_key_renders_without_null_key():
    # Outside production the gate does not apply. The env var is omitted
    # instead of rendering `key: null`.
    res = render(STAGING, "wildframe-staging", ["secrets.metricsTokenKey="])
    assert res.returncode == 0, res.stderr
    assert all(not entries for entries in metrics_token_refs(res.stdout).values())
