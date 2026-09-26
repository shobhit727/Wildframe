"""Behavioural tests for admin-service's five unmounted routers.

``app/main.py`` only mounts ``app.api.routes.admin``. The compliance and
transfer routers (documents / eu / india / transfers / processors) are
implemented but never included, so nothing exercised them.

They are mounted here into a *throwaway* ``FastAPI()`` instance — production
``main.py`` is left untouched (the user explicitly chose "no refactor") — and
driven over HTTP so route paths, status codes and the declared dependencies are
all verified, not just the handler bodies.
"""

import importlib
import sys

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db

ROUTER_MODULES = [
    "app.api.routes.documents",
    "app.api.routes.eu",
    "app.api.routes.india",
    "app.api.routes.transfers",
    "app.api.routes.processors",
]


class _RecordingSession:
    """Stands in for the AsyncSession the stub handlers would eventually use."""

    def __init__(self):
        self.used = False

    def __call__(self):
        self.used = True
        return self


def _declared_dependencies(module):
    """Dependency callables declared by the router's own routes.

    FastAPI 0.141 keeps included routers behind a lazy ``_IncludedRouter``,
    so ``app.routes`` cannot be introspected before the first request. The
    source router's routes hold the very same ``dependant.call`` objects that
    ``include_router`` copies, so overrides registered from them do apply.
    """
    calls = []
    for route in module.router.routes:
        for dep in getattr(route, "dependant", None).dependencies or ():
            calls.append(dep.call)
    return calls


def _build_client(module_name: str):
    module = importlib.import_module(module_name)
    throwaway = FastAPI()
    throwaway.include_router(module.router)
    session = _RecordingSession()
    # documents.py declares ``Depends(lambda: None)`` rather than get_db, so
    # override whatever callable the route actually declares.
    for call in _declared_dependencies(module):
        throwaway.dependency_overrides[call] = session
    client = AsyncClient(transport=ASGITransport(app=throwaway), base_url="http://stub")
    return throwaway, client, session


@pytest.fixture
async def make_client():
    clients = []

    def _factory(module_name):
        app, client, session = _build_client(module_name)
        clients.append(client)
        return client, session

    yield _factory
    for client in clients:
        await client.aclose()


class TestRoutersAreUnmountedInProduction:
    @pytest.mark.parametrize("module_name", ROUTER_MODULES)
    def test_router_is_not_reachable_from_the_production_app(self, module_name):
        from app.main import app as production_app

        # app.routes is lazily materialised in FastAPI 0.141, so the OpenAPI
        # schema is the reliable view of what the service actually serves.
        served = set(production_app.openapi()["paths"])
        module = importlib.import_module(module_name)
        paths = {r.path for r in module.router.routes}
        assert paths, module_name
        assert not (paths & served), (
            f"{module_name} is mounted in production main.py; this test file "
            "asserts these routers stay unmounted"
        )

    def test_production_app_serves_only_the_admin_router(self):
        from app.main import app as production_app

        served = set(production_app.openapi()["paths"])
        assert served == {
            "/api/v1/admin/alerts",
            "/api/v1/admin/alerts/critical",
            "/api/v1/admin/alerts/{alert_id}/acknowledge",
            "/api/v1/admin/audit/admin/{admin_id}",
            "/api/v1/admin/audit/resource/{resource_type}/{resource_id}",
            "/api/v1/admin/config",
            "/api/v1/admin/config/{key}",
            "/api/v1/admin/content/flag",
            "/api/v1/admin/content/flagged",
            "/api/v1/admin/content/resolve",
            "/api/v1/admin/stats",
            "/api/v1/admin/users/moderate",
            "/api/v1/admin/users/moderated",
            "/api/v1/admin/users/moderation/{user_id}",
            "/health",
            "/metrics",
            "/ready",
        }

    @pytest.mark.parametrize("module_name", ROUTER_MODULES)
    def test_router_declares_the_expected_prefix(self, module_name):
        module = importlib.import_module(module_name)
        assert module.router.prefix in {
            "/documents",
            "/eu",
            "/india",
            "/transfers",
            "/processors",
        }


class TestDocumentsRouter:
    async def test_create_document_returns_201_and_stored_flag(self, make_client):
        client, _ = make_client("app.api.routes.documents")
        resp = await client.post("/documents", json={"version": "2.1.0"})
        assert resp.status_code == 201
        assert resp.json() == {"id": "test", "version": "2.1.0", "stored": True}

    async def test_create_document_defaults_the_version(self, make_client):
        client, _ = make_client("app.api.routes.documents")
        resp = await client.post("/documents", json={})
        assert resp.status_code == 201
        assert resp.json()["version"] == "1.0.0"

    async def test_create_document_resolves_its_db_dependency(self, make_client):
        client, session = make_client("app.api.routes.documents")
        await client.post("/documents", json={})
        assert session.used is True

    async def test_create_document_declares_a_placeholder_dependency(self):
        # The handler takes ``db`` but ignores it; the route still resolves a
        # dependency, so overriding it must take effect.
        module = importlib.import_module("app.api.routes.documents")
        calls = _declared_dependencies(module)
        assert len(calls) == 1
        assert calls[0] is not get_db
        assert calls[0].__name__ == "<lambda>"

    async def test_accept_document_returns_201(self, make_client):
        client, _ = make_client("app.api.routes.documents")
        resp = await client.post("/documents/doc-42/accept", params={"user_id": "u-1"})
        assert resp.status_code == 201
        assert resp.json() == {
            "doc_id": "doc-42",
            "user_id": "u-1",
            "accepted": True,
            "audit_logged": True,
        }

    async def test_accept_document_requires_user_id(self, make_client):
        client, _ = make_client("app.api.routes.documents")
        resp = await client.post("/documents/doc-42/accept")
        assert resp.status_code == 422


class TestEuRouter:
    async def test_create_eu_reports_the_enabled_regimes(self, make_client):
        client, _ = make_client("app.api.routes.eu")
        resp = await client.post("/eu", json={"avms_enabled": True, "dsa_enabled": False})
        assert resp.status_code == 201
        assert resp.json() == {
            "id": "test",
            "avms": True,
            "dsa": False,
            "compliance": "AVMS+DSA+DMA",
        }

    async def test_create_eu_accepts_a_minimal_body(self, make_client):
        client, _ = make_client("app.api.routes.eu")
        resp = await client.post("/eu", json={})
        assert resp.status_code == 201
        assert resp.json()["avms"] is None
        assert resp.json()["dsa"] is None


class TestIndiaRouter:
    async def test_create_india_reports_the_officer_and_tier(self, make_client):
        client, _ = make_client("app.api.routes.india")
        resp = await client.post(
            "/india", json={"grievance_officer": "officer@example.com", "tier": "tier2"}
        )
        assert resp.status_code == 201
        assert resp.json() == {
            "id": "test",
            "grievance_officer": "officer@example.com",
            "tier": "tier2",
            "ott_compliance": True,
        }

    async def test_create_india_defaults_to_tier1(self, make_client):
        client, _ = make_client("app.api.routes.india")
        resp = await client.post("/india", json={})
        assert resp.status_code == 201
        assert resp.json()["tier"] == "tier1"


class TestTransfersRouter:
    async def test_create_transfer_reports_the_mechanism(self, make_client):
        client, _ = make_client("app.api.routes.transfers")
        resp = await client.post("/transfers", json={"mechanism": "BCR"})
        assert resp.status_code == 201
        assert resp.json() == {
            "id": "test",
            "mechanism": "BCR",
            "adequacy_check": True,
        }

    async def test_create_transfer_defaults_to_scc(self, make_client):
        client, _ = make_client("app.api.routes.transfers")
        resp = await client.post("/transfers", json={})
        assert resp.status_code == 201
        assert resp.json()["mechanism"] == "SCC"


class TestProcessorsRouter:
    async def test_create_processor_stores_the_dpa(self, make_client):
        client, _ = make_client("app.api.routes.processors")
        resp = await client.post("/processors", json={"name": "Acme Analytics"})
        assert resp.status_code == 201
        assert resp.json() == {"id": "test", "name": "Acme Analytics", "dpa_stored": True}

    async def test_create_processor_allows_a_missing_name(self, make_client):
        client, _ = make_client("app.api.routes.processors")
        resp = await client.post("/processors", json={})
        assert resp.status_code == 201
        assert resp.json()["name"] is None


class TestCrossRouterIsolation:
    async def test_mounting_all_five_together_keeps_prefixes_distinct(self):
        throwaway = FastAPI()
        for module_name in ROUTER_MODULES:
            throwaway.include_router(importlib.import_module(module_name).router)
        async with AsyncClient(
            transport=ASGITransport(app=throwaway), base_url="http://stub"
        ) as client:
            assert (await client.post("/documents", json={})).status_code == 201
            assert (await client.post("/eu", json={})).status_code == 201
            assert (await client.post("/india", json={})).status_code == 201
            assert (await client.post("/transfers", json={})).status_code == 201
            assert (await client.post("/processors", json={})).status_code == 201

    def test_routers_import_without_a_running_event_loop(self):
        # These modules must stay importable at app build time.
        for module_name in ROUTER_MODULES:
            assert importlib.import_module(module_name) in sys.modules.values()
