"""Tests for the routers that production ``main.py`` never mounts.

``rights.py``, ``dsar.py``, ``reviews.py`` and ``ads.py`` are mountable, so they
are driven over HTTP through a throwaway FastAPI app with ``get_db`` overridden
to a real PostgreSQL session (these tables use ``postgresql.UUID`` columns, so
SQLite is not an option).

The router still being unmounted in production is a separate decision: it has
its own authenticated surface to design, and mounting an unauthenticated
write endpoint is not a side effect of making the module importable.
"""

import importlib
import os
from contextlib import ExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pydantic
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import ads as ads_routes
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
    """Throwaway app mounting the four mountable compliance routers."""
    application = FastAPI()
    application.include_router(rights_routes.router)
    application.include_router(review_routes.router)
    application.include_router(dsar_routes.router)
    application.include_router(ads_routes.router)

    async def _override_get_db():
        yield session

    application.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as ac:
        yield ac


@asynccontextmanager
async def independent_reader(engine_url: str):
    """A session on its **own** engine, for reads that must not see the writer.

    The ``session`` fixture above is handed straight to ``get_db``, so every
    existing read-back in this file goes out over the same connection that
    issued the write. On that connection a ``flush()`` is indistinguishable
    from a ``commit()``: the row is in the transaction, the identity map and
    the session can see it, and nothing says whether anyone else can.

    A second engine guarantees a different connection, and PostgreSQL's READ
    COMMITTED isolation means it only ever sees committed rows. So "is this row
    visible to somebody else?" is a question this context manager can answer
    and the shared session cannot.
    """
    engine = create_async_engine(
        make_url(engine_url).set(drivername="postgresql+asyncpg"), echo=False
    )
    try:
        async with engine.begin() as conn:
            for base in BASES:
                await conn.run_sync(base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as fresh:
            yield fresh
    finally:
        await engine.dispose()


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
class TestAdsRouterIsImportable:
    """Reported bug: the module could not even be imported.

    ``create_ad`` annotated its request body as the SQLAlchemy
    ``DeclarativeBase`` subclass ``AdConfig``, so Pydantic could not build a
    field from it and the ``@router.post`` decorator raised ``FastAPIError``
    while building the route — before ``include_router`` was ever reached. The
    body is now the Pydantic ``app.schemas.ads.AdCreate``, translated to the
    ORM model inside the handler, so the module imports and mounts normally.
    """

    def test_the_module_imports_without_a_shim(self):
        # No sys.modules surgery and no injected ``__get_pydantic_core_schema__``:
        # a plain import is the whole claim.
        assert importlib.import_module("app.api.routes.ads") is ads_routes

    def test_the_request_annotation_is_not_the_sqlalchemy_model(self):
        import inspect

        annotation = inspect.signature(ads_routes.create_ad).parameters["request"].annotation

        assert annotation is not AdConfig
        assert hasattr(annotation, "model_fields")
        assert issubclass(annotation, pydantic.BaseModel)

    def test_the_router_declares_the_expected_paths(self):
        routes = {(r.path, tuple(sorted(r.methods))) for r in ads_routes.router.routes}

        assert ("/ads", ("POST",)) in routes
        assert ("/ads/check", ("GET",)) in routes


class TestAdsRouter:
    """Driven over HTTP, so the Pydantic body is actually decoded and validated."""

    async def test_create_ad_persists_a_consent_gated_config(self, client, session):
        content_id = uuid4()

        response = await client.post("/ads", json={"content_id": str(content_id)})

        assert response.status_code == 201
        body = response.json()
        assert body["id"]
        stored = await session.get(AdConfig, UUID(body["id"]))
        assert stored is not None
        assert stored.content_id == content_id
        assert stored.consent_gated is True
        assert stored.minor_safe is True
        assert stored.tcf_required is True

    async def test_create_ad_persists_explicit_flags(self, client, session):
        response = await client.post(
            "/ads",
            json={
                "content_id": str(uuid4()),
                "consent_gated": False,
                "tcf_required": False,
            },
        )

        assert response.status_code == 201
        stored = await session.get(AdConfig, UUID(response.json()["id"]))
        assert stored.consent_gated is False
        assert stored.tcf_required is False

    async def test_create_ad_commits_so_another_connection_can_read_the_row(
        self, client, engine_url
    ):
        """The row must be committed, not merely flushed.

        Every other read-back in this class uses ``session`` -- the very session
        ``get_db`` handed to the handler -- so ``await db.flush()`` on its own
        satisfies all of them: the pending INSERT sits in the writing
        transaction and is readable from that connection. Deleting the
        ``await db.commit()`` from ``create_ad`` therefore left the whole suite
        green while an ad config created over HTTP was invisible to every other
        connection, and lost outright on rollback.

        Read it back the way a different process would: a separate engine, so a
        different connection, where only committed rows exist.
        """
        content_id = uuid4()

        response = await client.post("/ads", json={"content_id": str(content_id)})

        assert response.status_code == 201
        ad_id = UUID(response.json()["id"])

        async with independent_reader(engine_url) as reader:
            stored = await reader.get(AdConfig, ad_id)

        assert stored is not None, (
            "the ad config is not visible to another connection: create_ad flushed "
            "but never committed, so the row exists only inside the request's "
            "transaction and is discarded on rollback"
        )
        assert stored.content_id == content_id
        assert stored.consent_gated is True

    @pytest.mark.parametrize(
        "payload",
        [{}, {"content_id": "not-a-uuid"}, {"content_id": str(uuid4()), "minor_safe": "maybe"}],
    )
    async def test_a_body_the_pydantic_schema_rejects_is_a_422(self, client, payload):
        # Proves the body is genuinely validated as a Pydantic model rather
        # than coerced onto the ORM instance.
        response = await client.post("/ads", json=payload)

        assert response.status_code == 422

    async def test_check_ad_allows_a_consented_request(self, client):
        response = await client.get(
            "/ads/check", params={"content_id": str(uuid4())}, headers={"X-Consent": "granted"}
        )

        assert response.status_code == 200
        assert response.json() == {"allowed": True}

    @pytest.mark.parametrize("consent", [None, ""])
    async def test_check_ad_blocks_without_consent(self, client, consent):
        headers = {"X-Consent": consent} if consent is not None else {}

        response = await client.get(
            "/ads/check", params={"content_id": str(uuid4())}, headers=headers
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Consent required"
