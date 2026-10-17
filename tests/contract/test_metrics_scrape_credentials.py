"""Contract checks that the /metrics guard and the Prometheus scraper agree.

All fifteen services gate ``/metrics`` behind ``METRICS_TOKEN`` whenever
``ENVIRONMENT`` is ``production``. The guard started as a seven-service
subset while Prometheus scraped all fifteen targets with no credential, so the
moment the environment was production those seven returned 401 and went dark
without any error surfacing anywhere. The other eight were the mirror-image
bug: an SDK-registered public ``/metrics`` route handing the full telemetry
payload to any anonymous caller.

These checks pin the four things that have to move together:

* every service gates ``/metrics``, with the expected set pinned explicitly so
  *removing* a guard fails here too;
* every gated service is handed ``METRICS_TOKEN`` by the dev compose file;
* the scrape job presents a bearer token and still scrapes all fifteen targets;
* the file Prometheus reads is bind-mounted, gitignored, and generated.

Deriving the expected set from the code alone is not enough: a guard deleted
from ``main.py`` would just shrink the derived set and pass quietly. The
explicit set below is what turns removal into a failure.
"""

from __future__ import annotations

import io
import re
import subprocess
import tokenize
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deployments" / "docker-compose.dev.yml"
PROMETHEUS_YML = REPO / "infrastructure" / "prometheus" / "prometheus.yml"
GITIGNORE = REPO / ".gitignore"
GEN_SCRIPT = REPO / "scripts" / "generate-dev-certs.sh"
TOKEN_FILE = REPO / "infrastructure" / "prometheus" / "metrics_bearer_token"

SERVICE_JOBS = {"wildframe-services"}

# Pinned deliberately rather than derived. ``_services_gating_metrics()`` alone
# cannot catch a deleted guard: the derived set would shrink in step with the
# regression and the test would stay green. This constant is the tripwire.
EXPECTED_GATED_SERVICES = {
    "admin-service",
    "analytics-service",
    "api-gateway",
    "auth-service",
    "billing-service",
    "content-service",
    "creators-service",
    "media-pipeline",
    "moderation-service",
    "notification-service",
    "recommendation-service",
    "search-service",
    "streaming-service",
    "uploads-service",
    "user-service",
}


def _all_services() -> set[str]:
    """Every service directory that ships an ASGI app."""
    return {m.parts[-3] for m in (REPO / "services").glob("*/app/main.py")}


def _services_gating_metrics() -> set[str]:
    """Return every service that gates /metrics on METRICS_TOKEN in its app."""
    gated: set[str] = set()
    for main in (REPO / "services").glob("*/app/main.py"):
        if "METRICS_TOKEN" in main.read_text(encoding="utf-8"):
            gated.add(main.parts[-3])
    return gated


def _guard_shape(service: str) -> str:
    """Return a service's ``main.py`` source with comments blanked out.

    The guard-shape assertions look for literal code fragments such as
    ``register_metrics=False``, which the explanatory comment above the call
    also contains. Matching against commented text would let a real regression
    pass on the strength of its own documentation.

    Comment spans are overwritten in place rather than the token stream being
    rejoined, because whitespace is not itself a token: rebuilding from
    ``token.string`` silently drops the spaces in ``settings.ENVIRONMENT ==
    "production"`` and the code-shaped assertions stop matching.
    """
    source = (REPO / "services" / service / "app/main.py").read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        row = token.start[0] - 1
        line = lines[row]
        start, end = token.start[1], token.end[1]
        lines[row] = line[:start] + " " * (end - start) + line[end:]
    return "".join(lines)


_GUARD_RE = re.compile(r"async def require_metrics_token\b.*?(?=\n[ \t]*\n)", re.DOTALL)


def _guard_body(service: str) -> str:
    """Return the ``require_metrics_token`` body with comments blanked out.

    Scoping to the function keeps the assertions honest: a module-wide search
    for ``settings.ENVIRONMENT == "production"`` also matches the docs_url and
    openapi_url lines every service carries, so a guard that had lost its own
    production check would still look compliant.
    """
    blanked = _guard_shape(service)
    match = _GUARD_RE.search(blanked)
    return match.group(0) if match else ""


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


def test_every_service_gates_metrics() -> None:
    """No service may be left exposing an unauthenticated /metrics.

    This is the assertion that fails when someone deletes a guard. The derived
    set is compared against the pinned one in both directions, so a removal and
    an unexpected addition are each a failure rather than a silent shrink.
    """
    gated = _services_gating_metrics()

    missing = EXPECTED_GATED_SERVICES - gated
    assert not missing, (
        f"{sorted(missing)} no longer gate /metrics; in production they would "
        "serve the full Prometheus payload to any anonymous caller"
    )

    unexpected = gated - EXPECTED_GATED_SERVICES
    assert not unexpected, (
        f"{sorted(unexpected)} gate /metrics but are not in the expected set; "
        "update EXPECTED_GATED_SERVICES deliberately"
    )

    assert gated == _all_services(), (
        "a service exists that does not appear in EXPECTED_GATED_SERVICES; every "
        "service is expected to gate /metrics"
    )


def test_each_guard_is_production_only_and_fail_closed() -> None:
    """The guard must be inert in dev and deny when the token is unset.

    Two properties are easy to break silently and both are security relevant:
    an unconditional guard locks local Prometheus out of the dev stack, and a
    guard that falls open when METRICS_TOKEN is missing turns a configuration
    mistake into a public metrics endpoint.

    The first two are asserted against the guard function alone. Searching the
    whole module would match unrelated occurrences -- every service hides its
    docs and schema behind ``settings.ENVIRONMENT == "production"`` -- and a
    guard that had lost its check would still pass.
    """
    for name in sorted(EXPECTED_GATED_SERVICES):
        guard = _guard_body(name)
        assert guard, f"{name} has no require_metrics_token function to check"

        assert 'settings.ENVIRONMENT == "production"' in guard, (
            f"{name} gates /metrics without an ENVIRONMENT check, so local "
            "Prometheus and the dev stack would be locked out"
        )
        assert "expected is None or authorization != expected" in guard, (
            f"{name} does not fail closed: an unset METRICS_TOKEN must deny the "
            "scrape rather than accept it"
        )
        assert "register_metrics=False" in _guard_shape(name), (
            f"{name} does not pass register_metrics=False, so the SDK's public "
            "/metrics route can shadow the guarded one"
        )


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


def test_scrape_job_covers_every_service() -> None:
    """A guard plus no scrape target is monitoring loss, not a fix.

    api-gateway is the cautionary case: it already shipped a /metrics route
    that the catch-all proxy shadowed into a 404, so it was listed as a scrape
    target while never actually being scraped.
    """
    config = yaml.safe_load(PROMETHEUS_YML.read_text(encoding="utf-8"))
    job = next(
        j for j in config["scrape_configs"] if j["job_name"] == "wildframe-services"
    )
    targets = {
        str(target).rsplit(":", 1)[0]
        for static in job.get("static_configs") or []
        for target in static.get("targets") or []
    }

    missing = _all_services() - targets
    assert not missing, (
        f"{sorted(missing)} are gated and carry a token but are absent from the "
        "wildframe-services scrape job, so they would go dark in production"
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
