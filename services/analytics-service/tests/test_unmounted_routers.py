"""Tests for the two routers that ``analytics-service/app/main.py`` never mounts.

``app/api/routes/dsar.py`` and ``app/api/routes/tracking.py`` define complete
routers with their own ``/dsar`` and ``/tracking`` prefixes, but
``create_app()`` only includes ``app.api.analytics_routes.router`` -- so
neither is reachable on the running service.

Per the "no refactor" decision these are **not** added to ``main.py``. They are
mounted onto a throwaway ``FastAPI()`` here so their handlers, status codes,
response shapes and the DSAR retention arithmetic are still executed.

Note ``app/api/routes/`` has no ``__init__.py`` -- it is an implicit namespace
package -- so the modules are imported as ``app.api.routes.dsar`` /
``app.api.routes.tracking``.

Neither router has a ``get_db`` dependency, so no session override is needed.
"""

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.routes import dsar as dsar_routes
from app.api.routes import tracking as tracking_routes


def build_app(router) -> FastAPI:
    """Mount one router on a throwaway app."""
    app = FastAPI()
    app.include_router(router)
    return app


# =========================== DSAR: /dsar/export ============================


@pytest.fixture
def dsar_client():
    app = build_app(dsar_routes.router)
    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.mark.unit
def test_dsar_router_is_not_mounted_in_production_app():
    """Pin why these tests use a throwaway app: the router is orphaned."""
    from app.api.analytics_routes import router as main_router

    assert not any(
        getattr(r, "path", "").startswith("/dsar") for r in main_router.routes
    )

    service_paths = set(main_module.create_app().openapi()["paths"])
    assert not any(p.startswith("/dsar") for p in service_paths)
    assert not any(p.startswith("/tracking") for p in service_paths)


@pytest.mark.unit
def test_dsar_router_declares_its_prefix_tags_and_routes():
    """The router is fully formed -- only the mount is missing."""
    assert dsar_routes.router.prefix == "/dsar"
    assert dsar_routes.router.tags == ["analytics-dsar"]
    by_path = {getattr(r, "path", ""): r for r in dsar_routes.router.routes}
    assert set(by_path) == {"/dsar/export", "/dsar/retention-check"}


@pytest.mark.unit
def test_export_returns_one_sla_compliant_record(dsar_client):
    """GET /export -> a single record echoing the requested user."""
    user_id = uuid4()
    r = dsar_client.get("/dsar/export", params={"user_id": str(user_id)})

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    record = body[0]
    assert record["user_id"] == str(user_id)
    assert record["export_format"] == "json"
    assert record["retention_days"] == 365
    assert record["sla_compliant"] is True
    assert record["data"] == "[]"
    assert UUID(record["id"])
    assert UUID(record["dsar_id"])
    assert record["created_at"]


@pytest.mark.unit
def test_export_ids_are_unique_per_request(dsar_client):
    """Each export mints fresh ids -- a replayed DSAR must not collide."""
    first = dsar_client.get("/dsar/export", params={"user_id": str(uuid4())}).json()[0]
    second = dsar_client.get("/dsar/export", params={"user_id": str(uuid4())}).json()[0]

    assert first["id"] != second["id"]
    assert first["dsar_id"] != second["dsar_id"]


@pytest.mark.unit
def test_export_defaults_to_the_json_format(dsar_client):
    """The ``format`` query defaults to json."""
    r = dsar_client.get("/dsar/export", params={"user_id": str(uuid4())})
    assert r.json()[0]["export_format"] == "json"


@pytest.mark.unit
def test_export_accepts_the_csv_format(dsar_client):
    """The pattern also permits csv, and the value is echoed through."""
    r = dsar_client.get(
        "/dsar/export", params={"user_id": str(uuid4()), "format": "csv"}
    )
    assert r.status_code == 200
    assert r.json()[0]["export_format"] == "csv"


@pytest.mark.unit
@pytest.mark.parametrize("fmt", ["xml", "jsonl", "JSON", "", "yaml"])
def test_export_rejects_an_unsupported_format(dsar_client, fmt):
    """Anything outside ``^(json|csv)$`` is a 422, not a silent default."""
    r = dsar_client.get(
        "/dsar/export", params={"user_id": str(uuid4()), "format": fmt}
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_export_requires_a_user_id(dsar_client):
    """``user_id`` is a required query parameter."""
    r = dsar_client.get("/dsar/export")
    assert r.status_code == 422
    assert any(e["loc"][-1] == "user_id" for e in r.json()["detail"])


@pytest.mark.unit
def test_export_rejects_a_malformed_user_id(dsar_client):
    """A non-UUID user_id is rejected at the schema layer."""
    r = dsar_client.get("/dsar/export", params={"user_id": "not-a-uuid"})
    assert r.status_code == 422


# ====================== DSAR: /dsar/retention-check ========================


@pytest.mark.unit
def test_retention_check_is_compliant_below_the_policy(dsar_client):
    """retention_days <= 2555 -> compliant, no action required."""
    user_id = uuid4()
    r = dsar_client.get(
        "/dsar/retention-check",
        params={"user_id": str(user_id), "retention_days": 365},
    )

    assert r.status_code == 200
    assert r.json() == {
        "user_id": str(user_id),
        "retention_days": 365,
        "max_retention": 2555,
        "compliant": True,
        "required_action": None,
    }


@pytest.mark.unit
def test_retention_check_is_compliant_at_exactly_the_policy_limit(dsar_client):
    """2555 is the documented maximum and must still be compliant."""
    r = dsar_client.get(
        "/dsar/retention-check",
        params={"user_id": str(uuid4()), "retention_days": 2555},
    )
    assert r.status_code == 200
    assert r.json()["compliant"] is True
    assert r.json()["required_action"] is None


@pytest.mark.unit
def test_retention_check_flags_one_day_over_the_limit(dsar_client):
    """2556 is the first non-compliant value."""
    r = dsar_client.get(
        "/dsar/retention-check",
        params={"user_id": str(uuid4()), "retention_days": 2556},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["compliant"] is False
    assert body["required_action"] == "reduce_retention_period"
    assert body["max_retention"] == 2555


@pytest.mark.unit
def test_retention_check_flags_a_far_future_retention(dsar_client):
    """A long retention period is denied with the same required action."""
    r = dsar_client.get(
        "/dsar/retention-check",
        params={"user_id": str(uuid4()), "retention_days": 100_000},
    )
    body = r.json()
    assert body["compliant"] is False
    assert body["required_action"] == "reduce_retention_period"
    assert body["retention_days"] == 100_000


@pytest.mark.unit
def test_retention_check_allows_a_zero_day_retention(dsar_client):
    """Zero is below the limit, so it is compliant."""
    r = dsar_client.get(
        "/dsar/retention-check",
        params={"user_id": str(uuid4()), "retention_days": 0},
    )
    assert r.json()["compliant"] is True


@pytest.mark.unit
def test_retention_check_requires_both_parameters(dsar_client):
    """Both ``user_id`` and ``retention_days`` are mandatory."""
    assert dsar_client.get("/dsar/retention-check").status_code == 422
    r = dsar_client.get("/dsar/retention-check", params={"user_id": str(uuid4())})
    assert r.status_code == 422
    assert any(e["loc"][-1] == "retention_days" for e in r.json()["detail"])


@pytest.mark.unit
def test_retention_check_rejects_a_malformed_user_id(dsar_client):
    """A non-UUID user_id is rejected at the schema layer."""
    r = dsar_client.get(
        "/dsar/retention-check",
        params={"user_id": "nope", "retention_days": 30},
    )
    assert r.status_code == 422


# ============================== tracking ====================================


@pytest.fixture
def tracking_client():
    app = build_app(tracking_routes.router)
    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.mark.unit
def test_tracking_router_declares_its_prefix_tags_and_status_code():
    """The tracking router is fully formed -- only the mount is missing."""
    assert tracking_routes.router.prefix == "/tracking"
    assert tracking_routes.router.tags == ["tracking"]
    routes = list(tracking_routes.router.routes)
    assert len(routes) == 1
    assert getattr(routes[0], "path", "") == "/tracking"
    assert routes[0].status_code == 201


@pytest.mark.unit
def test_create_tracking_returns_201_with_the_stub_body(tracking_client):
    """POST "" -> 201 with the documented stub response."""
    r = tracking_client.post("/tracking", json={})

    assert r.status_code == 201
    assert r.json() == {
        "id": "test",
        "consent_mode": "denied",
        "sdk_governed": True,
        "retention": 365,
    }


@pytest.mark.unit
def test_create_tracking_echoes_the_supplied_consent_mode(tracking_client):
    """``consent_mode`` is taken from the body when present."""
    r = tracking_client.post("/tracking", json={"consent_mode": "granted"})

    assert r.status_code == 201
    body = r.json()
    assert body["consent_mode"] == "granted"
    # The rest of the stub is constant.
    assert body["id"] == "test"
    assert body["sdk_governed"] is True
    assert body["retention"] == 365


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["denied", "granted", "pending", "anything"])
def test_create_tracking_accepts_any_consent_mode(tracking_client, mode):
    """The mode is a free-form pass-through with no validation."""
    r = tracking_client.post("/tracking", json={"consent_mode": mode})
    assert r.status_code == 201
    assert r.json()["consent_mode"] == mode


@pytest.mark.unit
def test_create_tracking_defaults_to_denied_when_the_key_is_absent(tracking_client):
    """The documented default is 'denied' -- fail-closed on missing consent."""
    r = tracking_client.post("/tracking", json={"other": "value"})
    assert r.status_code == 201
    assert r.json()["consent_mode"] == "denied"


@pytest.mark.unit
def test_create_tracking_returns_null_for_an_explicit_null_consent_mode(tracking_client):
    """QUIRK -- app/api/routes/tracking.py:10.

    ``body.get("consent_mode", "denied")`` only applies the ``"denied"``
    fallback when the key is *absent*. A client that sends an explicit
    ``{"consent_mode": null}`` gets ``null`` echoed back, not the fail-closed
    ``"denied"`` default. Harmless for a stub, but it means the default is
    absent-key-only rather than null-safe.

    Pinned so the behaviour is explicit; if the handler is hardened to
    ``body.get("consent_mode") or "denied"``, this test fails.
    """
    r = tracking_client.post("/tracking", json={"consent_mode": None})

    assert r.status_code == 201
    assert r.json()["consent_mode"] is None


@pytest.mark.unit
def test_create_tracking_rejects_a_non_object_body(tracking_client):
    """``body: dict`` -- a list or scalar is a 422, not a crash."""
    assert tracking_client.post("/tracking", json=["a"]).status_code == 422
    assert tracking_client.post("/tracking", json="a").status_code == 422


@pytest.mark.unit
def test_tracking_and_dsar_can_coexist_on_one_app():
    """Both orphaned routers mount cleanly together under their prefixes."""
    app = build_app(dsar_routes.router)
    app.include_router(tracking_routes.router)
    with TestClient(app, base_url="http://localhost") as c:
        assert c.post("/tracking", json={}).status_code == 201
        assert c.get("/dsar/export", params={"user_id": str(uuid4())}).status_code == 200
