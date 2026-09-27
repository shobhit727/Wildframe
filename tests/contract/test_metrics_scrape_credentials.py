"""Contract checks that the /metrics guard and the Prometheus scraper agree.

Seven services gate ``/metrics`` behind ``METRICS_TOKEN`` whenever
``ENVIRONMENT`` is ``production``. Prometheus scraped all fifteen targets with
no credential, so the moment the environment was production those seven
returned 401 and went dark without any error surfacing anywhere.

These checks pin the three things that have to move together:

* every service whose ``main.py`` gates ``/metrics`` is handed ``METRICS_TOKEN``
  by the dev compose file;
* the scrape job for those services presents a bearer token;
* the file Prometheus reads is bind-mounted, gitignored, and generated.

The first test is the one that matters -- it is derived from the services
themselves, so adding an eighth gated service without wiring its credential
fails here instead of going dark in a running stack.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deployments" / "docker-compose.dev.yml"
PROMETHEUS_YML = REPO / "infrastructure" / "prometheus" / "prometheus.yml"
GITIGNORE = REPO / ".gitignore"
GEN_SCRIPT = REPO / "scripts" / "generate-dev-certs.sh"
TOKEN_FILE = REPO / "infrastructure" / "prometheus" / "metrics_bearer_token"

SERVICE_JOBS = {"wildframe-services"}


def _services_gating_metrics() -> set[str]:
    """Return every service that gates /metrics on METRICS_TOKEN in its app."""
    gated: set[str] = set()
    for main in (REPO / "services").glob("*/app/main.py"):
        if "METRICS_TOKEN" in main.read_text(encoding="utf-8"):
            gated.add(main.parts[-3])
    return gated


def _environment(service: dict) -> dict[str, str]:
    """Normalise a compose ``environment`` block, which may be a list or a map."""
    env = service.get("environment") or {}
    if isinstance(env, list):
        out: dict[str, str] = {}
        for item in env:
            key, _, value = str(item).partition("=")
            out[key] = value
        return out
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def test_gated_services_receive_a_metrics_token() -> None:
    """Every /metrics-gated service must be given METRICS_TOKEN by compose.

    Without this the guard compares against an empty string and rejects every
    scrape, which is a silent monitoring outage rather than a loud failure.
    """
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    gated = _services_gating_metrics()

    assert gated, "no service gates /metrics; this test's premise has changed"

    for name in sorted(gated):
        assert name in services, f"{name} gates /metrics but is absent from compose"
        token = _environment(services[name]).get("METRICS_TOKEN")
        assert token, (
            f"{name} gates /metrics on METRICS_TOKEN but compose does not set it; "
            "Prometheus would be locked out of that service in production"
        )


def test_scrape_job_presents_a_bearer_token() -> None:
    """The scrape job for the gated services must carry a bearer credential."""
    config = yaml.safe_load(PROMETHEUS_YML.read_text(encoding="utf-8"))
    jobs = {j["job_name"]: j for j in config["scrape_configs"]}

    assert SERVICE_JOBS <= set(jobs), f"missing scrape job(s): {SERVICE_JOBS - set(jobs)}"
    for name in SERVICE_JOBS:
        token_file = jobs[name].get("bearer_token_file")
        assert token_file, (
            f"scrape job {name!r} has no bearer_token_file, so the seven gated "
            "services 401 and disappear from monitoring"
        )


def test_bearer_token_file_is_mounted_into_prometheus() -> None:
    """The referenced token file must actually reach the container."""
    config = yaml.safe_load(PROMETHEUS_YML.read_text(encoding="utf-8"))
    job = next(
        j for j in config["scrape_configs"] if j["job_name"] == "wildframe-services"
    )
    container_path = str(job["bearer_token_file"])

    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    volumes = compose["services"]["prometheus"].get("volumes") or []
    mounts = [v for v in volumes if container_path in str(v)]

    assert mounts, (
        f"prometheus.yml reads {container_path} but no prometheus volume mounts it; "
        "Prometheus fails to start on a missing bearer token file"
    )
    assert any(":ro" in str(m) for m in mounts), (
        f"{container_path} should be mounted read-only"
    )


def test_token_file_is_generated_and_never_committed() -> None:
    """A credential must not be committed, and must be produced by the bootstrap."""
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(TOKEN_FILE.relative_to(REPO))],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode != 0, (
        f"{TOKEN_FILE.relative_to(REPO)} is committed; it is a bearer credential"
    )

    ignored = TOKEN_FILE.relative_to(REPO).as_posix() in GITIGNORE.read_text(
        encoding="utf-8"
    )
    assert ignored, "the generated metrics token is not gitignored"

    assert "ensure_metrics_token" in GEN_SCRIPT.read_text(encoding="utf-8"), (
        "nothing generates the bearer token file Prometheus is told to read"
    )


def test_generator_writes_the_token_without_a_trailing_newline() -> None:
    """Prometheus sends the file contents verbatim as the bearer credential.

    A trailing newline would become part of the token, so the service would
    compute a different expected value and reject every scrape with a 401 that
    looks like a bad token rather than a formatting bug.
    """
    script = GEN_SCRIPT.read_text(encoding="utf-8")

    assert """printf '%s' "$token" > "$METRICS_TOKEN_FILE\"""" in script, (
        "the token must be written with no trailing newline"
    )
    assert "${METRICS_TOKEN:-" in script, (
        "METRICS_TOKEN has no default, so a clean checkout gets an empty token "
        "and the guard rejects every scrape"
    )
