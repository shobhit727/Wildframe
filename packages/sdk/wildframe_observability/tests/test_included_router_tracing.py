"""Regression test for #978: `include_router` routes must not 500 under tracing.

`opentelemetry-instrumentation-fastapi` 0.49b0 resolved the span's route by
iterating ``app.routes`` and reading ``starlette_route.path``. FastAPI 0.137+
nests routes added with ``include_router()`` under ``_IncludedRouter`` tree
nodes, which expose no ``path``, so the instrumentation raised::

    File ".../instrumentation/fastapi/__init__.py", line 427, in _get_route_details
        route = starlette_route.path
    AttributeError: '_IncludedRouter' object has no attribute 'path'

...on every request to an included route, and the request 500'd.

Why this test exists at all: #978 was invisible to CI. The backend suites call
route handlers through dependency overrides and never put the ASGI + OTel
middleware stack in front of an ``include_router`` route, and the Playwright
suite mocks the API outright. Both were green while the real API returned 500
on `/api/v1/auth/login`. A pin bump alone would leave that gap open, so the
defect is pinned here as behaviour rather than as a version number.

The test drives a real request through the real middleware, because the whole
failure mode is an interaction between the ASGI middleware, the router tree and
the instrumentation. Asserting on the installed version instead would pass
again the moment someone bumps it to another incompatible line.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def traced_client():
    """A TestClient over an app whose routes are added with include_router.

    Mirrors how the services actually compose: a router per area, included
    into the app, with FastAPIInstrumentor in front of the whole thing.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    area = APIRouter(prefix="/api/v1")

    @area.get("/things/{thing_id}")
    async def read_thing(thing_id: int) -> dict:
        return {"id": thing_id}

    app = FastAPI()
    app.include_router(area)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    with TestClient(app) as client:
        yield client, exporter
    FastAPIInstrumentor.uninstrument_app(app)


def test_included_route_responds_and_records_its_path(traced_client) -> None:
    """The exact #978 failure: an included route must answer, not raise.

    `/health` returns 200 even on the broken pin because a plain route has a
    ``.path``; only the included route exercised the defect. That asymmetry is
    why the issue describes a service whose health check passes while every
    real endpoint 500s, so both are asserted here.
    """
    client, exporter = traced_client

    assert client.get("/health").status_code == 200

    response = client.get("/api/v1/things/7")
    assert response.status_code == 200, (
        "an include_router route 500'd under the FastAPI instrumentation, which "
        "is #978: the instrumentation is reading .path off an _IncludedRouter"
    )
    assert response.json() == {"id": 7}


def test_included_route_emits_a_span_naming_the_route(traced_client) -> None:
    """The span must carry the templated path, not a missing or wrong one.

    Silently degrading to an unnamed span would hide the regression behind
    green tests, so the attribute is asserted directly.
    """
    client, exporter = traced_client
    client.get("/api/v1/things/7")

    spans = [s for s in exporter.get_finished_spans() if s.name.startswith("GET")]
    assert spans, "no server span recorded for the included route"

    names = {s.name for s in spans}
    assert any("things" in n for n in names), (
        f"expected a span naming the included route, got {sorted(names)}"
    )

    routes = {
        s.attributes.get("http.route")
        for s in spans
        if s.attributes
    }
    assert any(r and "things" in str(r) for r in routes), (
        f"span recorded no usable http.route for the included route: {routes}"
    )


def test_pinned_instrumentation_tolerates_included_routers() -> None:
    """Guard the version line that fixes it, with the reason attached.

    A bare "assert version >= X" would not survive a future bump, and would not
    say why. This states the property: the resolver must not read ``.path``
    off a node that has none.
    """
    from opentelemetry.instrumentation import fastapi as inst

    source = (
        inst.__file__ and __import__("pathlib").Path(inst.__file__).read_text("utf-8")
    )
    assert "_IncludedRouter" in source or "iter_route_contexts" in source, (
        "the installed opentelemetry-instrumentation-fastapi does not handle "
        "_IncludedRouter, so include_router routes will 500 again (#978). "
        "0.49b0 and other pre-0.6x lines read starlette_route.path directly."
    )
    assert "starlette_route.path" not in source or "_flatten_routes" in source, (
        "the resolver reads starlette_route.path without flattening the router "
        "tree first, which is the #978 defect"
    )
