"""Contract checks for service startup and readiness probes."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
VALUES = REPO / "infrastructure" / "helm" / "wildframe" / "values.yaml"


def test_every_service_uses_liveness_for_startup_and_readiness_for_traffic():
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    defaults = values["probes"]

    assert values["services"]
    for service_name, service in values["services"].items():
        overrides = service.get("probes", {})
        startup = {**defaults["startupProbe"], **overrides.get("startupProbe", {})}
        readiness = {**defaults["readinessProbe"], **overrides.get("readinessProbe", {})}

        assert startup["path"] == "/health", service_name
        assert readiness["path"] == "/ready", service_name
