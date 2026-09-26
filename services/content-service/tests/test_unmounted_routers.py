"""Tests for the routers that production ``main.py`` never mounts.

``rights.py``, ``dsar.py`` and ``reviews.py`` are mountable, so they are driven
over HTTP through a throwaway FastAPI app with ``get_db`` overridden to a real
PostgreSQL session (these tables use ``postgresql.UUID`` columns, so SQLite is
not an option).

``ads.py`` cannot be imported at all: ``create_ad`` annotates its request body
as the SQLAlchemy ``DeclarativeBase`` subclass ``AdConfig``, so the
``@router.post`` decorator raises ``FastAPIError`` while building the route.
That is reported in :class:`TestAdsRouterIsUnimportable`. The handlers are still
reachable — the decorator hands back the undecorated coroutine — so the
behaviour itself is covered by calling them directly, using a test-only
Pydantic shim that lets the module import.
"""

import importlib
import os
import sys
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import dsar as dsar_routes
from app.api.routes import reviews as review_routes
from app.api.routes import rights as rights_routes
from app.core.database import get_db
from app.models import ads as app_models_ads
from app.models import dsar as app_models_dsar
from app.models import reviews as app_models_reviews
from app.models import rights as app_models_rights
from app.models.ads import AdConfig
from app.models.dsar import ContentDSARRecord
from app.models.reviews import Review
from app.models.rights import RightsHolder, TerritorialLicense

pytestmark = pytest.mark.unit

# Each compliance module owns its own DeclarativeBase (and therefore its own
# metadata), so the tables have to be created per base.
BASES = [
    app_models_rights.Base,
    app_models_reviews.Base,
    app_models_dsar.Base,
    app_models_ads.Base,
]


LOCAL_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:test@127.0.0.1:55432/test_db"


def _local_instance_reachable(url: str) -> bool:
    """Cheap TCP probe so the suite also works without TEST_DATABASE_URL set."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((parsed.hostname, parsed.port or 5432)) == 0


@pytest.fixture(scope="module")
def engine_url():
    """TEST_DATABASE_URL, the documented local instance, else a container."""
    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url and _local_instance_reachable(LOCAL_TEST_DATABASE_URL):
            url = LOCAL_TEST_DATABASE_URL
        if not url:
            from testcontainers.postgres import PostgresContainer  # lazy import

            postgres = stack.enter_context(PostgresContainer("postgres:15"))
            url = postgres.get_connection_url()
        yield url


@pytest_asyncio.fixture
async def session(engine_url):
    """Real session with every compliance table present (create_all is checkfirst)."""
    engine = create_async_engine(
        make_url(engine_url).set(drivername="postgresql+asyncpg"), echo=False
    )
    async with engine.begin() as conn:
        for base in BASES:
            await conn.run_sync(base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session):
    """Throwaway app mounting the three mountable compliance routers."""
    application = FastAPI()
    application.include_router(rights_routes.router)
    application.include_router(review_routes.router)
    application.include_router(dsar_routes.router)

    async def _override_get_db():
        yield session

    application.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as ac:
        yield ac


# ------------------------------------------------------------------ rights router
class TestRightsRouter:
    async def test_create_holder_persists_and_echoes_the_payload(self, client, session):
        payload = {"name": "Studio Aurora", "type": "studio", "contact": "legal@aurora.test"}

        response = await client.post("/rights/holders", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Studio Aurora"
        assert body["type"] == "studio"
        assert body["contact"] == "legal@aurora.test"

        stored = await session.get(RightsHolder, UUID(body["id"]))
        assert stored is not None
        assert stored.name == "Studio Aurora"

    async def test_create_holder_defaults_contact_to_null(self, client, session):
        response = await client.post("/rights/holders", json={"name": "Indie", "type": "creator"})

        assert response.status_code == 201
        assert response.json()["contact"] is None
        stored = await session.get(RightsHolder, UUID(response.json()["id"]))
        assert stored.contact is None

    async def test_create_holder_requires_name_and_type(self, client):
        response = await client.post("/rights/holders", json={"type": "studio"})

        assert response.status_code == 422

    async def test_create_license_persists_window_and_royalty(self, client, session):
        holder = await client.post(
            "/rights/holders", json={"name": "Licensor", "type": "distributor"}
        )
        holder_id = holder.json()["id"]
        avail_start = datetime.now(UTC)
        avail_end = avail_start + timedelta(days=180)
        payload = {
            "content_id": str(uuid4()),
            "rights_holder_id": holder_id,
            "territory": "US",
            "exclusive": True,
            "avail_start": avail_start.isoformat(),
            "avail_end": avail_end.isoformat(),
            "royalty_rate": "0.45",
        }

        response = await client.post("/rights/licenses", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["territory"] == "US"
        assert body["royalty_rate"] == "0.45"

        stored = await session.get(TerritorialLicense, UUID(body["id"]))
        assert stored is not None
        assert stored.rights_holder_id == UUID(holder_id)
        assert stored.exclusive is True

    async def test_create_license_defaults_exclusive_and_royalty_rate(self, client, session):
        holder = await client.post("/rights/holders", json={"name": "D", "type": "creator"})
        payload = {
            "content_id": str(uuid4()),
            "rights_holder_id": holder.json()["id"],
            "territory": "IN",
            "avail_start": datetime.now(UTC).isoformat(),
            "avail_end": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        }

        response = await client.post("/rights/licenses", json=payload)

        assert response.status_code == 201
        assert response.json()["exclusive"] is True
        assert response.json()["royalty_rate"] == "0.30"
        stored = await session.get(TerritorialLicense, UUID(response.json()["id"]))
        assert stored.territory == "IN"

    async def test_create_license_rejects_an_unparsable_window(self, client):
        holder = await client.post("/rights/holders", json={"name": "E", "type": "creator"})
        payload = {
            "content_id": str(uuid4()),
            "rights_holder_id": holder.json()["id"],
            "territory": "EU",
            "avail_start": "not-a-date",
            "avail_end": "also-not-a-date",
        }

        response = await client.post("/rights/licenses", json=payload)

        assert response.status_code == 422


# ----------------------------------------------------------------- reviews router
class TestReviewsRouter:
    async def test_create_review_marks_the_viewer_as_verified(self, client, session):
        content_id = uuid4()
        user_id = uuid4()
        payload = {
            "content_id": str(content_id),
            "user_id": str(user_id),
            "rating": 4,
            "text": "Solid pacing",
        }

        response = await client.post("/reviews", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["verified"] is True

        stored = await session.get(Review, UUID(body["id"]))
        assert stored is not None
        assert stored.verified_viewer is True
        assert stored.rating == 4
        assert stored.text == "Solid pacing"
        assert stored.content_id == content_id
        assert stored.user_id == user_id

    async def test_create_review_keeps_visibility_defaults(self, client, session):
        payload = {
            "content_id": str(uuid4()),
            "user_id": str(uuid4()),
            "rating": 1,
            "text": "Not for me",
        }

        response = await client.post("/reviews", json=payload)

        stored = await session.get(Review, UUID(response.json()["id"]))
        assert stored.is_visible is True
        assert stored.moderated is False
        assert stored.helpful_votes == 0

    @pytest.mark.parametrize("rating", [0, 6, -1])
    async def test_rating_outside_one_to_five_is_rejected(self, client, rating):
        payload = {
            "content_id": str(uuid4()),
            "user_id": str(uuid4()),
            "rating": rating,
            "text": "x",
        }

        response = await client.post("/reviews", json=payload)

        assert response.status_code == 422

    async def test_missing_text_is_rejected(self, client):
        response = await client.post(
            "/reviews",
            json={"content_id": str(uuid4()), "user_id": str(uuid4()), "rating": 3},
        )

        assert response.status_code == 422


# -------------------------------------------------------------------- dsar router
class TestDsarRouter:
    async def test_content_export_is_empty_without_records(self, client):
        user_id = uuid4()
        dsar_id = uuid4()

        response = await client.get(
            "/dsar/content", params={"user_id": str(user_id), "dsar_id": str(dsar_id)}
        )

        assert response.status_code == 200
        assert response.json() == []

    async def test_content_export_returns_the_matching_records(self, client, session):
        user_id = uuid4()
        dsar_id = uuid4()
        record = ContentDSARRecord(
            user_id=user_id,
            dsar_id=dsar_id,
            content_type="reviews",
            export_data='{"reviews": 3}',
        )
        session.add(record)
        await session.commit()

        response = await client.get(
            "/dsar/content", params={"user_id": str(user_id), "dsar_id": str(dsar_id)}
        )

        body = response.json()
        assert response.status_code == 200
        assert len(body) == 1
        assert body[0]["content_type"] == "reviews"
        assert body[0]["export_data"] == '{"reviews": 3}'
        assert body[0]["user_id"] == str(user_id)

    async def test_content_export_is_scoped_to_the_dsar_request(self, client, session):
        user_id = uuid4()
        session.add(
            ContentDSARRecord(
                user_id=user_id,
                dsar_id=uuid4(),
                content_type="uploads",
                export_data="{}",
            )
        )
        await session.commit()

        response = await client.get(
            "/dsar/content", params={"user_id": str(user_id), "dsar_id": str(uuid4())}
        )

        assert response.json() == []

    async def test_content_export_rejects_a_malformed_user_id(self, client):
        response = await client.get(
            "/dsar/content", params={"user_id": "nope", "dsar_id": str(uuid4())}
        )

        assert response.status_code == 422

    async def test_full_export_returns_the_portability_envelope(self, client):
        user_id = uuid4()

        response = await client.get(f"/dsar/export/{user_id}")

        assert response.status_code == 200
        assert response.json() == {
            "user_id": str(user_id),
            "viewing_history": [],
            "uploads": [],
            "reviews": [],
        }


# --------------------------------------------------------------------- ads router
def _make_ads_module_importable():
    """Give ``AdConfig`` a Pydantic core schema so the route can be declared.

    Purely a test-side shim: production ``ads.py`` is left untouched and stays
    unimportable (see TestAdsRouterIsUnimportable).
    """
    from pydantic_core import core_schema

    def __get_pydantic_core_schema__(cls, source_type, handler):
        return core_schema.is_instance_schema(cls)

    AdConfig.__get_pydantic_core_schema__ = classmethod(__get_pydantic_core_schema__)
    sys.modules.pop("app.api.routes.ads", None)
    return importlib.import_module("app.api.routes.ads")


@pytest.fixture(scope="module")
def ads_module():
    module = _make_ads_module_importable()
    yield module
    del AdConfig.__get_pydantic_core_schema__
    sys.modules.pop("app.api.routes.ads", None)


class TestAdsRouterIsUnimportable:
    """Reported bug: the module cannot even be imported.

    ``create_ad`` declares ``request: AdConfig`` where ``AdConfig`` is a
    SQLAlchemy ``DeclarativeBase`` subclass, so the ``@router.post`` decorator
    fails while building the route — before ``include_router`` is ever reached.
    """

    def test_import_raises_a_fastapi_error(self):
        sys.modules.pop("app.api.routes.ads", None)

        with pytest.raises(Exception) as exc:
            importlib.import_module("app.api.routes.ads")

        assert "Invalid args for response field" in str(exc.value)
        assert "AdConfig" in str(exc.value)

    def test_the_annotation_is_a_sqlalchemy_declarative_base(self):
        from sqlalchemy.orm import DeclarativeBase

        assert issubclass(AdConfig, DeclarativeBase)
        assert not hasattr(AdConfig, "model_fields")

    def test_include_router_is_unreachable(self):
        # Documents *where* the failure does NOT happen: mounting never gets a
        # chance, the module-level decorator blows up first.
        sys.modules.pop("app.api.routes.ads", None)
        with pytest.raises(Exception) as exc:
            importlib.import_module("app.api.routes.ads")
        assert exc.type.__name__ == "FastAPIError"


class TestAdsHandlers:
    """Direct calls on the handler coroutines (the shimmed module's endpoints)."""

    async def test_create_ad_persists_a_consent_gated_config(self, ads_module, session):
        config = AdConfig(content_id=uuid4())

        result = await ads_module.create_ad(config, session)

        assert result == {"id": str(config.id)}
        stored = await session.get(AdConfig, config.id)
        assert stored is not None
        assert stored.content_id == config.content_id
        assert stored.consent_gated is True
        assert stored.minor_safe is True
        assert stored.tcf_required is True

    async def test_create_ad_persists_explicit_flags(self, ads_module, session):
        config = AdConfig(content_id=uuid4(), consent_gated=False, tcf_required=False)

        await ads_module.create_ad(config, session)

        stored = await session.get(AdConfig, config.id)
        assert stored.consent_gated is False
        assert stored.tcf_required is False

    async def test_check_ad_allows_a_consented_request(self, ads_module):
        assert await ads_module.check_ad(uuid4(), "granted") == {"allowed": True}

    @pytest.mark.parametrize("consent", [None, ""])
    async def test_check_ad_blocks_without_consent(self, ads_module, consent):
        with pytest.raises(HTTPException) as exc:
            await ads_module.check_ad(uuid4(), consent)

        assert exc.value.status_code == 403
        assert exc.value.detail == "Consent required"

    def test_the_router_declares_the_expected_paths(self, ads_module):
        routes = {(route.path, tuple(sorted(route.methods))) for route in ads_module.router.routes}

        assert ("/ads", ("POST",)) in routes
        assert ("/ads/check", ("GET",)) in routes
