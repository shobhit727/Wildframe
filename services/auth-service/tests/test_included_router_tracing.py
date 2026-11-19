"""The real auth app's include_router routes must survive FastAPI tracing.

Regression guard for #978, re-checked for the OpenTelemetry family move to
1.45.2 / 0.66b2 in #1163. ``opentelemetry-instrumentation-fastapi`` resolves a
span's route in ``_get_route_details()``. Releases before 0.64b0 read
``scope["route"].path`` unguarded, and FastAPI nests ``include_router`` routes
under an ``_IncludedRouter`` that has no ``.path``, so every included route
answered HTTP 500 once tracing was on.

The SDK tests cover a synthetic app. This one instruments the app that
``create_app()`` actually builds, whose ``/api/v1/auth/*`` routes are mounted
through two levels of ``include_router`` (``app`` -> ``api_router`` ->
``auth_router``), and drives requests through the real ASGI stack with the real
instrumentor and an in-memory exporter. Nothing in the OTel path is mocked.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import create_app


@pytest.fixture
def traced_app() -> Iterator[tuple[FastAPI, InMemorySpanExporter]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app()
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    try:
        yield app, exporter
    finally:
        FastAPIInstrumentor.uninstrument_app(app)


def _server_routes(exporter: InMemorySpanExporter) -> set[str]:
    return {
        str(span.attributes["http.route"])
        for span in exporter.get_finished_spans()
        if span.attributes and "http.route" in span.attributes
    }


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "expected_status"),
    [
        # No bearer token: the auth dependency rejects before any DB access.
        ("get", "/api/v1/auth/me", {}, 401),
        # Empty body: request validation rejects before the handler runs.
        ("post", "/api/v1/auth/register", {"json": {}}, 422),
    ],
)
def test_included_route_is_not_500_under_tracing(
    traced_app, method, path, kwargs, expected_status
):
    app, exporter = traced_app
    # Not entered as a context manager, so the lifespan (DB, Kafka) never runs:
    # these requests are rejected before they would need either.
    client = TestClient(app, base_url="http://localhost")

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == expected_status, (
        f"{method.upper()} {path} answered {response.status_code} under tracing. "
        "A 500 means opentelemetry-instrumentation-fastapi cannot resolve an "
        "_IncludedRouter in scope['route'] (issue #978)."
    )
    # The span carries the route template that _get_route_details() resolved
    # through both include_router levels. This proves the instrumentor ran on
    # this request and walked the nested router, so the status above is not a
    # vacuous pass through an uninstrumented app.
    assert path in _server_routes(exporter), (
        f"no span recorded http.route={path}; spans: {_server_routes(exporter)}"
    )
