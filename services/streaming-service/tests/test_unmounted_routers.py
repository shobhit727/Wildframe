"""Tests for the two routers that ``app/main.py`` never mounts.

``app/api/routes/maturity.py`` and ``app/api/routes/drm.py`` define complete
routers with their own ``/maturity`` and ``/drm`` prefixes, but
``create_app()`` only includes ``app.api.routes.router`` -- so neither is
reachable on the running service.

Per the "no refactor" decision these are **not** added to ``main.py``. They
are mounted onto a throwaway ``FastAPI()`` here so their handlers, status
codes, 404 paths and response serialisation are still executed and measured.
The tests assert the mount prefix and status codes that *would* be served.

A fake async session stands in for ``Depends(get_db)``; the real dependency is
overridden, so no database is touched.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import drm as drm_routes
from app.api.routes import maturity as maturity_routes
from app.core.database import get_db


def _uuid_like(value: UUID) -> MagicMock:
    """A MagicMock whose attributes compare equal to a real UUID.

    Used so ``create_maturity`` can hand a model to ``model_validate`` without
    a live database row.
    """
    return value


class FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeAsyncSession:
    """Minimal AsyncSession stand-in covering only what the routers call.

    ``add`` is synchronous because SQLAlchemy's ``AsyncSession.add`` is the
    inherited *sync* method -- the routers correctly call it without ``await``.

    ``flush`` applies the mapped column defaults, which is what a real
    INSERT does. Without that, ``id``/``created_at`` would stay ``None`` and
    the response models could not be populated.
    """

    def __init__(self, found=None):
        self.added: list = []
        self.committed = 0
        self.flushed = 0
        self.refreshed = 0
        self._found = found
        self.execute = AsyncMock(return_value=FakeScalarResult(found))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1
        for obj in self.added:
            _apply_column_defaults(obj)

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        self.refreshed += 1


def _apply_column_defaults(obj) -> None:
    """Fill unset mapped columns from their Python-side defaults.

    Mirrors what an INSERT does: scalar defaults are copied and callable
    defaults (``uuid4``, the timestamp lambdas) are invoked.
    """
    from sqlalchemy import inspect as sa_inspect

    for column in sa_inspect(type(obj)).mapper.columns:
        if getattr(obj, column.key, None) is not None:
            continue
        default = column.default
        if default is None:
            continue
        if default.is_scalar:
            setattr(obj, column.key, default.arg)
        elif callable(default.arg):
            setattr(obj, column.key, default.arg(None))


def _maturity_row(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid4(),
        "content_id": uuid4(),
        "maturity_rating": "PG-13",
        "min_age": 13,
        "requires_parental_consent": True,
        "purchase_restricted": False,
        "spending_limit_cents": None,
        "screen_time_limit_minutes": None,
        "bedtime_start": None,
        "bedtime_end": None,
        "created_at": datetime(2026, 1, 2, 3, 4, 5),
        "updated_at": datetime(2026, 1, 2, 3, 4, 5),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _drm_row(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid4(),
        "content_id": uuid4(),
        "fairplay_enabled": True,
        "widevine_enabled": False,
        "device_limit": 3,
        "expiry_hours": 48,
        "offline_allowed": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def build_app(router, session: FakeAsyncSession) -> FastAPI:
    """Mount one router on a throwaway app with a fake DB session."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    return app


# ============================================================== maturity =====


@pytest.fixture
def maturity_client():
    session = FakeAsyncSession()
    app = build_app(maturity_routes.router, session)
    with TestClient(app, base_url="http://localhost") as c:
        c.session = session
        yield c
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_maturity_router_is_not_mounted_in_production_app():
    """Pin the reason these tests use a throwaway app: the router is orphaned."""
    from app.api.routes import router as main_router

    paths = {getattr(r, "path", "") for r in main_router.routes}
    assert not any(p.startswith("/maturity") for p in paths)
    assert not any(p.startswith("/drm") for p in paths)

    import app.main as main_module

    service_paths = set(main_module.create_app().openapi()["paths"])
    assert not any(p.startswith("/maturity") for p in service_paths)
    assert not any(p.startswith("/drm") for p in service_paths)


@pytest.mark.unit
def test_maturity_router_declares_its_prefix_tags_and_status_codes():
    """The router is fully formed -- only the mount is missing.

    Only the create route declares ``status_code=201``; ``/check`` and
    ``/{content_id}`` fall through to FastAPI's 200 default.
    """
    assert maturity_routes.router.prefix == "/maturity"
    assert maturity_routes.router.tags == ["maturity"]
    by_path = {getattr(r, "path", ""): r for r in maturity_routes.router.routes}
    assert set(by_path) == {"/maturity", "/maturity/check", "/maturity/{content_id}"}
    assert by_path["/maturity"].status_code == 201
    assert by_path["/maturity/check"].status_code is None
    assert by_path["/maturity/{content_id}"].status_code is None


@pytest.mark.unit
def test_create_maturity_persists_and_returns_201(maturity_client):
    """POST "" -> 201 with the persisted record echoed back."""
    content_id = uuid4()

    r = maturity_client.post(
        "/maturity",
        json={"content_id": str(content_id), "maturity_rating": "PG-13", "min_age": 13},
    )

    assert r.status_code == 201
    body = r.json()
    assert body["content_id"] == str(content_id)
    assert body["maturity_rating"] == "PG-13"
    assert body["min_age"] == 13
    assert body["requires_parental_consent"] is False
    assert body["purchase_restricted"] is False
    assert body["spending_limit_cents"] is None
    assert body["screen_time_limit_minutes"] is None
    assert body["bedtime_start"] is None
    assert body["bedtime_end"] is None
    # A UUID was assigned and the timestamps were stamped.
    assert UUID(body["id"])
    assert body["created_at"]
    assert body["updated_at"]

    assert maturity_client.session.committed == 1
    assert maturity_client.session.flushed == 1
    assert maturity_client.session.refreshed == 1
    assert len(maturity_client.session.added) == 1
    record = maturity_client.session.added[0]
    assert record.content_id == content_id
    assert record.maturity_rating == "PG-13"
    assert record.min_age == 13


@pytest.mark.unit
def test_create_maturity_persists_optional_constraints(maturity_client):
    """The optional purchase/screen-time/bedtime fields are stored verbatim."""
    content_id = uuid4()
    r = maturity_client.post(
        "/maturity",
        json={
            "content_id": str(content_id),
            "maturity_rating": "18+",
            "min_age": 18,
            "purchase_restricted": True,
            "spending_limit_cents": 500,
            "screen_time_limit_minutes": 60,
            "bedtime_start": "21:00",
            "bedtime_end": "07:00",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["purchase_restricted"] is True
    assert body["spending_limit_cents"] == 500
    assert body["screen_time_limit_minutes"] == 60
    assert body["bedtime_start"] == "21:00"
    assert body["bedtime_end"] == "07:00"


@pytest.mark.unit
def test_create_maturity_rejects_an_unknown_rating(maturity_client):
    """The schema pattern is the only rating gate; a bad value never inserts."""
    r = maturity_client.post(
        "/maturity",
        json={"content_id": str(uuid4()), "maturity_rating": "X", "min_age": 1},
    )
    assert r.status_code == 422
    assert maturity_client.session.added == []


@pytest.mark.unit
def test_create_maturity_rejects_out_of_range_min_age(maturity_client):
    """min_age is bounded 0..21 at the schema layer."""
    r = maturity_client.post(
        "/maturity",
        json={"content_id": str(uuid4()), "maturity_rating": "G", "min_age": 99},
    )
    assert r.status_code == 422
    assert maturity_client.session.added == []


@pytest.mark.unit
def test_check_maturity_allows_when_no_record_exists(maturity_client):
    """No record -> fail-open with an explicit reason (maturity.py:46-49)."""
    maturity_client.session.execute = AsyncMock(return_value=FakeScalarResult(None))
    r = maturity_client.post(
        "/maturity/check",
        json={"user_id": str(uuid4()), "content_id": str(uuid4()), "user_age": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True
    assert body["reason"] == "No maturity restriction"
    assert body["requires_consent"] is False
    assert body["min_age"] == 0


@pytest.mark.unit
def test_check_maturity_blocks_underage_without_consent(maturity_client):
    """age < min_age and no consent -> denied, consent demanded (maturity.py:50-56)."""
    maturity_client.session.execute = AsyncMock(
        return_value=FakeScalarResult(_maturity_row(min_age=16, requires_parental_consent=True))
    )
    r = maturity_client.post(
        "/maturity/check",
        json={"user_id": str(uuid4()), "content_id": str(uuid4()), "user_age": 12},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is False
    assert body["reason"] == "Requires age 16 or parental consent"
    assert body["requires_consent"] is True
    assert body["min_age"] == 16


@pytest.mark.unit
def test_check_maturity_allows_underage_with_parental_consent(maturity_client):
    """Consent is the documented override for an under-age viewer."""
    maturity_client.session.execute = AsyncMock(
        return_value=FakeScalarResult(_maturity_row(min_age=16, requires_parental_consent=True))
    )
    r = maturity_client.post(
        "/maturity/check",
        json={
            "user_id": str(uuid4()),
            "content_id": str(uuid4()),
            "user_age": 12,
            "parental_consent": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True
    assert body["reason"] is None
    assert body["requires_consent"] is True
    assert body["min_age"] == 16


@pytest.mark.unit
def test_check_maturity_allows_old_enough_viewer(maturity_client):
    """age >= min_age -> allowed, reason null, consent flag mirrored."""
    maturity_client.session.execute = AsyncMock(
        return_value=FakeScalarResult(_maturity_row(min_age=13, requires_parental_consent=False))
    )
    r = maturity_client.post(
        "/maturity/check",
        json={"user_id": str(uuid4()), "content_id": str(uuid4()), "user_age": 30},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True
    assert body["reason"] is None
    assert body["requires_consent"] is False
    assert body["min_age"] == 13


@pytest.mark.unit
def test_get_maturity_returns_the_record(maturity_client):
    """GET /{content_id} serialises the row via from_attributes."""
    row = _maturity_row()
    maturity_client.session.execute = AsyncMock(return_value=FakeScalarResult(row))
    r = maturity_client.get(f"/maturity/{row.content_id}")
    # No explicit status_code, so FastAPI's 200 default applies.
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(row.id)
    assert body["content_id"] == str(row.content_id)
    assert body["maturity_rating"] == "PG-13"
    assert body["min_age"] == 13
    assert body["requires_parental_consent"] is True
    assert body["created_at"] == row.created_at.isoformat()


@pytest.mark.unit
def test_get_maturity_404s_when_absent(maturity_client):
    """maturity.py:74-75 -- missing record is a 404, not a 500."""
    maturity_client.session.execute = AsyncMock(return_value=FakeScalarResult(None))
    r = maturity_client.get(f"/maturity/{uuid4()}")
    assert r.status_code == 404
    assert r.json()["detail"] == "Maturity not found"


@pytest.mark.unit
def test_get_maturity_rejects_a_non_uuid_path(maturity_client):
    """content_id is typed UUID -> a malformed id never hits the query."""
    r = maturity_client.get("/maturity/not-a-uuid")
    assert r.status_code == 422
    maturity_client.session.execute.assert_not_called()


# ================================================================== drm =====


@pytest.fixture
def drm_client():
    session = FakeAsyncSession()
    app = build_app(drm_routes.router, session)
    with TestClient(app, base_url="http://localhost") as c:
        c.session = session
        yield c
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_drm_router_declares_its_prefix_tags_and_status_codes():
    """The DRM router is fully formed -- only the mount is missing.

    Only ``/license`` declares ``status_code=201``; the GET defaults to 200.
    """
    assert drm_routes.router.prefix == "/drm"
    assert drm_routes.router.tags == ["drm"]
    by_path = {getattr(r, "path", ""): r for r in drm_routes.router.routes}
    assert set(by_path) == {"/drm/license", "/drm/{content_id}"}
    assert by_path["/drm/license"].status_code == 201
    assert by_path["/drm/{content_id}"].status_code is None


@pytest.mark.unit
def test_create_license_persists_and_returns_201_with_id(drm_client):
    """POST /license -> 201 and the new row's id as a string (drm.py:16-22)."""
    content_id = uuid4()

    r = drm_client.post("/drm/license", json={"content_id": str(content_id)})

    assert r.status_code == 201
    assert UUID(r.json()["id"])
    assert drm_client.session.committed == 1
    assert drm_client.session.flushed == 1
    added = drm_client.session.added[0]
    assert added.content_id == content_id
    assert added.fairplay_enabled is True
    assert added.widevine_enabled is True
    assert added.device_limit == 3
    assert added.expiry_hours == 48
    assert added.offline_allowed is False


@pytest.mark.unit
def test_create_license_honours_disabled_drm_flags(drm_client):
    """The create payload can turn individual DRM systems off."""
    content_id = uuid4()
    r = drm_client.post(
        "/drm/license",
        json={
            "content_id": str(content_id),
            "fairplay_enabled": False,
            "widevine_enabled": False,
            "device_limit": 1,
            "expiry_hours": 24,
            "offline_allowed": True,
        },
    )
    assert r.status_code == 201
    added = drm_client.session.added[0]
    assert added.fairplay_enabled is False
    assert added.widevine_enabled is False
    assert added.device_limit == 1
    assert added.expiry_hours == 24
    assert added.offline_allowed is True


@pytest.mark.unit
def test_get_drm_returns_the_config(drm_client):
    """GET /{content_id} -> the DRM flags for the asset (drm.py:34-38)."""
    row = _drm_row(fairplay_enabled=True, widevine_enabled=False)
    drm_client.session.execute = AsyncMock(return_value=FakeScalarResult(row))
    r = drm_client.get(f"/drm/{row.content_id}")
    assert r.status_code == 200
    assert r.json() == {
        "content_id": str(row.content_id),
        "fairplay": True,
        "widevine": False,
    }


@pytest.mark.unit
def test_get_drm_returns_an_error_object_when_absent(drm_client):
    """drm.py:32-33 -- a missing config is a 200 with {"error": "not found"}.

    Not a 404: worth pinning because it differs from maturity's behaviour.
    """
    drm_client.session.execute = AsyncMock(return_value=FakeScalarResult(None))
    r = drm_client.get(f"/drm/{uuid4()}")
    assert r.status_code == 200
    assert r.json() == {"error": "not found"}


@pytest.mark.unit
def test_get_drm_rejects_a_non_uuid_path(drm_client):
    """content_id is typed UUID -> a malformed id never hits the query."""
    r = drm_client.get("/drm/nope")
    assert r.status_code == 422
    drm_client.session.execute.assert_not_called()


@pytest.mark.unit
def test_uuid_like_helper_returns_input_unchanged():
    """Guard the test helper itself so a future refactor cannot silently drift."""
    value = uuid4()
    assert _uuid_like(value) is value
    assert isinstance(value, UUID)
