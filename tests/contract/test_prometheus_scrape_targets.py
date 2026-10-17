"""The Prometheus scrape list must match the gated services exactly.

`test_metrics_scrape_credentials.py` proves each gated service is *given* a
credential and that the scrape job presents one. It does not prove the two
sides still refer to the same set of services, which is the failure that
produces a silently broken dashboard rather than a red build:

* a service gains a `/metrics` guard and nobody adds it to `prometheus.yml`, so
  it stops being monitored and nothing complains;
* a target stays listed after a service is de-gated, so Prometheus keeps
  scraping something that now answers 401;
* a target's port drifts from the port compose actually maps, and the scrape
  silently 404s.

Every one of those is invisible to a test that only checks the credential
plumbing, so the target list is pinned against the code here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deployments" / "docker-compose.dev.yml"
PROMETHEUS = REPO / "infrastructure" / "prometheus" / "prometheus.yml"

JOB = "wildframe-services"


def _gated_services() -> set[str]:
    """Services whose app disables the SDK metrics route and guards its own."""
    gated = set()
    for main in (REPO / "services").glob("*/app/main.py"):
        if "register_metrics=False" in main.read_text(encoding="utf-8"):
            # relative_to(REPO), not parts[1]: these are absolute paths, so a
            # fixed index lands on a parent of the repo root rather than the
            # service name.
            gated.add(main.relative_to(REPO).parts[1])
    assert gated, "no service gates /metrics; this test's premise has changed"
    return gated


def _targets() -> dict[str, str]:
    config = yaml.safe_load(PROMETHEUS.read_text(encoding="utf-8"))
    job = next(j for j in config["scrape_configs"] if j["job_name"] == JOB)
    out = {}
    for entry in job["static_configs"][0]["targets"]:
        service, _, port = entry.partition(":")
        out[service] = port
    return out


def test_scrape_targets_are_exactly_the_gated_services() -> None:
    """No service may be gated without a target, or targeted without a guard."""
    gated, targets = _gated_services(), set(_targets())

    assert not (gated - targets), (
        f"gated but never scraped, so their metrics are invisible: "
        f"{sorted(gated - targets)}"
    )
    assert not (targets - gated), (
        f"scraped but no longer gated, so Prometheus will get 401 from: "
        f"{sorted(targets - gated)}"
    )


def test_every_target_names_a_service_that_exists() -> None:
    """A typo'd hostname is a target that can never resolve."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    missing = sorted(set(_targets()) - set(services))
    assert not missing, f"prometheus.yml scrapes services absent from compose: {missing}"


# There is deliberately no port-agreement test here.
#
# An earlier version of this file had one, comparing each target port against the
# port compose publishes. It could never fail: the fifteen scraped services have
# no `ports:` entry at all -- they are reachable only inside the Docker network
# as `service:port`, with Caddy as the single host-exposed entry point. Only
# postgres, redis, kafka, prometheus and friends publish a mapping, and none of
# them appear in the wildframe-services job.
#
# So there is no published port for a target to disagree with, and a check
# against it passes vacuously while looking like coverage. Writing it would have
# repeated the exact failure this file exists to prevent: a guard derived from
# data that cannot vary.
#
# If ports are ever published for these services, add the check then -- with a
# test that has been mutation-verified to fail on a real mismatch.
