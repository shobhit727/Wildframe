#!/usr/bin/env python3
"""Chart policy test: baseline security/resilience controls must stay in the chart.

Runs without helm (source-level assertions) so it can gate CI and local
development equally. If helm is available it additionally renders the chart
and verifies the rendered manifests carry the controls.

Usage:
    python3 tests/test_security_policy.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parent.parent / "wildframe"
TEMPLATES = CHART / "templates"

DEPLOYMENT_REQUIRED = [
    ("runAsNonRoot enforcement", "runAsNonRoot: true"),
    ("seccomp RuntimeDefault", "seccompProfile:"),
    ("seccomp profile type", "type: RuntimeDefault"),
    ("privilege escalation denied", "allowPrivilegeEscalation: false"),
    ("capabilities dropped", "capabilities:"),
    ("drop ALL capabilities", "- ALL"),
    ("token automount disabled", "automountServiceAccountToken:"),
    ("graceful termination", "terminationGracePeriodSeconds:"),
    ("startup probe", "startupProbe:"),
    ("rolling update strategy", "maxSurge:"),
    ("rolling update bounds", "maxUnavailable:"),
    ("resource bounds", "resources:"),
]

POLICY_REQUIRED = [
    ("ingress policy type", "Ingress"),
    ("egress policy type", "Egress"),
]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> int:
    values = yaml.safe_load((CHART / "values.yaml").read_text())

    # --- values-level invariants -------------------------------------
    strategy = values.get("strategy", {})
    assert "maxSurge" in strategy, "values.yaml: strategy.maxSurge missing"
    assert "maxUnavailable" in strategy, "values.yaml: strategy.maxUnavailable missing"
    assert values.get("terminationGracePeriodSeconds", 0) >= 30, (
        "values.yaml: terminationGracePeriodSeconds must give workers time to finish"
    )
    assert values.get("automountServiceAccountToken") is False, (
        "values.yaml: automountServiceAccountToken must default to false"
    )
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert probe in values.get("probes", {}), f"values.yaml: probes.{probe} missing"
    resources = values.get("resources", {})
    assert "requests" in resources and "limits" in resources, (
        "values.yaml: global resources must define requests and limits"
    )
    services = values.get("services", {})
    assert services, "values.yaml: services must not be empty"
    for name, svc in services.items():
        assert "containerPort" in svc, f"services.{name}: containerPort missing"
        # Every workload must resolve meaningful requests+limits (own or global).
        own = svc.get("resources")
        req = (own or resources).get("requests", {})
        lim = (own or resources).get("limits", {})
        assert req.get("cpu") and req.get("memory"), f"services.{name}: CPU/memory requests missing"
        assert lim.get("cpu") and lim.get("memory"), f"services.{name}: CPU/memory limits missing"

    # --- template-level invariants ------------------------------------
    deployment = (TEMPLATES / "deployment.yaml").read_text()
    for label, fragment in DEPLOYMENT_REQUIRED:
        assert fragment in deployment, f"templates/deployment.yaml: {label} ({fragment!r}) stripped"

    for template in ("networkpolicy.yaml", "default-deny.yaml"):
        text = (TEMPLATES / template).read_text()
        for label, fragment in POLICY_REQUIRED:
            assert fragment in text, f"templates/{template}: {label} stripped"

    pdb = (TEMPLATES / "pdb.yaml").read_text()
    assert "PodDisruptionBudget" in pdb and "minAvailable" in pdb, (
        "templates/pdb.yaml: PDB coverage stripped"
    )
    assert "hardcoded secrets" not in values, "values.yaml: hardcoded secrets not allowed"

    # --- helm render check (when helm is available) --------------------
    helm = subprocess.run(["helm", "version", "--short"], capture_output=True, text=True)
    if helm.returncode != 0:
        print("PASS (source-level; helm not installed, render check skipped)")
        return 0
    for values_file in (None, CHART.parent / "values-staging.yaml", CHART.parent / "values-production.yaml"):
        cmd = ["helm", "template", "wildframe", str(CHART), "--namespace", "wildframe"]
        if values_file is not None:
            cmd += ["-f", str(values_file)]
        rendered = subprocess.run(cmd, capture_output=True, text=True)
        assert rendered.returncode == 0, f"helm template failed: {rendered.stderr}"
        body = rendered.stdout
        for fragment in ("runAsNonRoot: true", "allowPrivilegeEscalation: false", "- ALL",
                         "type: RuntimeDefault", "automountServiceAccountToken: false",
                         "kind: PodDisruptionBudget", "kind: NetworkPolicy"):
            assert fragment in body, f"rendered manifest missing {fragment!r}"
    print("PASS (source-level + helm render with default/staging/production values)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())