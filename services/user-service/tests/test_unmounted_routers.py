"""Tests for the user-service routers that `app/main.py` never mounts.

`app/api/routes/child.py`, `privacy.py` and `dsar.py` each build an APIRouter
but `create_app()` only includes `app.api.routes.router`. Nothing in the repo
mounts them, so 99 lines of handlers had no coverage at all.

Each router is mounted here into a THROWAWAY `FastAPI()` with the real
dependency providers overridden by a real aiosqlite session - i.e. the handler
code (validation, repository calls, status codes, response serialisation) runs
for real, only the transport is stubbed. Production `main.py` is untouched.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.child import router as child_router
from app.api.routes.dsar import get_dsar_repo, router as dsar_router
from app.api.routes.privacy import get_consent_repo, router as privacy_router
from app.core.database import get_db, get_db_session
from app.models import Base
from app.repositories.dsar import DSARRepository
from app.repositories.privacy import UserConsentRepository


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "routers.db"


@pytest_asyncio.fixture
async def sqlite_session(db_path):
    """A real (file-backed) SQLite session with the user-service schema."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def child_client(sqlite_session):
    app = FastAPI()
    app.include_router(child_router)
    app.dependency_overrides[get_db_session] = lambda: sqlite_session
    with TestClient(app) as client:
        yield client


@pytest.fixture
def privacy_client(sqlite_session):
    app = FastAPI()
    app.include_router(privacy_router)
    app.dependency_overrides[get_db] = lambda: sqlite_session
    app.dependency_overrides[get_consent_repo] = lambda: UserConsentRepository(sqlite_session)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def dsar_client(sqlite_session):
    app = FastAPI()
    app.include_router(dsar_router)
    app.dependency_overrides[get_db_session] = lambda: sqlite_session
    app.dependency_overrides[get_dsar_repo] = lambda: DSARRepository(sqlite_session)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# child.py
# ---------------------------------------------------------------------------


def test_child_router_is_prefixed_child_accounts():
    assert child_router.prefix == "/child-accounts"


def test_create_child_account_returns_201(child_client):
    child_id, parent_id = uuid4(), uuid4()

    response = child_client.post(
        "/child-accounts",
        json={
            "child_user_id": str(child_id),
            "parent_user_id": str(parent_id),
            "relationship": "guardian",
            "verification_method": "document",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["child_user_id"] == str(child_id)
    assert body["parent_user_id"] == str(parent_id)
    assert body["relationship"] == "guardian"
    assert body["verification_method"] == "document"
    # Consent must never start out verified.
    assert body["parental_consent_verified"] is False
    assert body["verified_at"] is None
    assert body["is_active"] is True


def test_create_child_account_applies_schema_defaults(child_client):
    child_id, parent_id = uuid4(), uuid4()

    response = child_client.post(
        "/child-accounts",
        json={"child_user_id": str(child_id), "parent_user_id": str(parent_id)},
    )

    assert response.status_code == 201
    assert response.json()["relationship"] == "parent"
    assert response.json()["verification_method"] == "email_otp"


def test_create_child_account_rejects_a_duplicate_link(child_client):
    child_id, parent_id = uuid4(), uuid4()
    payload = {"child_user_id": str(child_id), "parent_user_id": str(parent_id)}
    assert child_client.post("/child-accounts", json=payload).status_code == 201

    response = child_client.post(
        "/child-accounts", json={**payload, "parent_user_id": str(uuid4())}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Child already linked"


@pytest.mark.parametrize(
    "field,value",
    [
        ("relationship", "sibling"),
        ("verification_method", "telepathy"),
    ],
)
def test_create_child_account_rejects_unknown_enum_values(child_client, field, value):
    payload = {
        "child_user_id": str(uuid4()),
        "parent_user_id": str(uuid4()),
        field: value,
    }

    assert child_client.post("/child-accounts", json=payload).status_code == 422


def test_verify_parental_consent_sets_the_timestamp(child_client):
    child_id, parent_id = uuid4(), uuid4()
    created = child_client.post(
        "/child-accounts",
        json={"child_user_id": str(child_id), "parent_user_id": str(parent_id)},
    ).json()

    response = child_client.post(f"/child-accounts/{created['id']}/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["parental_consent_verified"] is True
    assert body["verified_at"] is not None


def test_verify_parental_consent_404s_for_an_unknown_child(child_client):
    response = child_client.post(f"/child-accounts/{uuid4()}/verify")

    assert response.status_code == 404
    assert response.json()["detail"] == "Child account not found"


def test_list_children_returns_only_the_requested_parents_active_rows(child_client):
    parent_id = uuid4()
    other_parent = uuid4()
    for _ in range(2):
        child_client.post(
            "/child-accounts",
            json={"child_user_id": str(uuid4()), "parent_user_id": str(parent_id)},
        )
    child_client.post(
        "/child-accounts",
        json={"child_user_id": str(uuid4()), "parent_user_id": str(other_parent)},
    )

    response = child_client.get(f"/child-accounts/{parent_id}")

    assert response.status_code == 200
    listed = response.json()
    assert len(listed) == 2
    assert {row["parent_user_id"] for row in listed} == {str(parent_id)}


def test_list_children_excludes_deactivated_rows(child_client, db_path):
    from app.models.child_account import ChildAccount

    parent_id = uuid4()
    child_client.post(
        "/child-accounts",
        json={"child_user_id": str(uuid4()), "parent_user_id": str(parent_id)},
    )

    # Flip is_active from a *separate* engine: the handler runs in the
    # TestClient's event loop, so its session must not be reused here.
    async def _deactivate() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ChildAccount).where(ChildAccount.parent_user_id == parent_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            for row in rows:
                row.is_active = False
            await session.commit()
        await engine.dispose()

    asyncio.run(_deactivate())

    response = child_client.get(f"/child-accounts/{parent_id}")

    assert response.json() == []


def test_list_children_returns_an_empty_list_for_an_unknown_parent(child_client):
    response = child_client.get(f"/child-accounts/{uuid4()}")

    assert response.status_code == 200
    assert response.json() == []


def test_list_children_rejects_a_non_uuid_parent(child_client):
    assert child_client.get("/child-accounts/not-a-uuid").status_code == 422


# ---------------------------------------------------------------------------
# privacy.py
# ---------------------------------------------------------------------------


def test_privacy_router_is_prefixed_privacy():
    assert privacy_router.prefix == "/privacy"


def test_create_consent_records_granted_at_when_granted(privacy_client):
    user_id = uuid4()

    response = privacy_client.post(
        "/privacy/consent",
        json={
            "user_id": str(user_id),
            "consent_type": "marketing",
            "jurisdiction": "EU",
            "granted": True,
            "version": "1.0.0",
            "ip_address": "203.0.113.7",
            "user_agent": "pytest",
            "consent_metadata": '{"source":"banner"}',
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert body["granted"] is True
    assert body["granted_at"] is not None
    assert body["withdrawn_at"] is None
    assert body["ip_address"] == "203.0.113.7"
    assert body["consent_metadata"] == '{"source":"banner"}'


def test_create_consent_leaves_granted_at_null_when_refused(privacy_client):
    response = privacy_client.post(
        "/privacy/consent",
        json={
            "user_id": str(uuid4()),
            "consent_type": "analytics",
            "jurisdiction": "US",
            "granted": False,
            "version": "1.0.0",
        },
    )

    assert response.status_code == 201
    assert response.json()["granted_at"] is None


def test_create_consent_conflicts_on_a_duplicate_type_and_jurisdiction(privacy_client):
    user_id = uuid4()
    payload = {
        "user_id": str(user_id),
        "consent_type": "cookies",
        "jurisdiction": "EU",
        "granted": True,
        "version": "1.0.0",
    }
    assert privacy_client.post("/privacy/consent", json=payload).status_code == 201

    response = privacy_client.post("/privacy/consent", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "Consent already exists"


def test_create_consent_allows_the_same_type_in_another_jurisdiction(privacy_client):
    user_id = uuid4()
    base = {
        "user_id": str(user_id),
        "consent_type": "cookies",
        "granted": True,
        "version": "1.0.0",
    }

    eu = privacy_client.post("/privacy/consent", json={**base, "jurisdiction": "EU"})
    us = privacy_client.post("/privacy/consent", json={**base, "jurisdiction": "US"})

    assert eu.status_code == 201
    assert us.status_code == 201


def test_list_consent_returns_the_users_records(privacy_client):
    user_id, other_user = uuid4(), uuid4()
    for consent_type in ("marketing", "analytics"):
        privacy_client.post(
            "/privacy/consent",
            json={
                "user_id": str(user_id),
                "consent_type": consent_type,
                "jurisdiction": "EU",
                "granted": True,
                "version": "1.0.0",
            },
        )
    privacy_client.post(
        "/privacy/consent",
        json={
            "user_id": str(other_user),
            "consent_type": "marketing",
            "jurisdiction": "EU",
            "granted": True,
            "version": "1.0.0",
        },
    )

    response = privacy_client.get("/privacy/consent", params={"user_id": str(user_id)})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {row["user_id"] for row in body} == {str(user_id)}


def test_list_consent_is_newest_first(privacy_client):
    user_id = uuid4()
    for consent_type in ("marketing", "analytics", "profiling"):
        privacy_client.post(
            "/privacy/consent",
            json={
                "user_id": str(user_id),
                "consent_type": consent_type,
                "jurisdiction": "EU",
                "granted": True,
                "version": "1.0.0",
            },
        )

    body = privacy_client.get("/privacy/consent", params={"user_id": str(user_id)}).json()

    assert [row["consent_type"] for row in body] == ["profiling", "analytics", "marketing"]


def test_preference_center_lists_records_and_available_types(privacy_client):
    user_id = uuid4()
    privacy_client.post(
        "/privacy/consent",
        json={
            "user_id": str(user_id),
            "consent_type": "marketing",
            "jurisdiction": "EU",
            "granted": True,
            "version": "1.0.0",
        },
    )

    response = privacy_client.get("/privacy/preferences", params={"user_id": str(user_id)})

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert len(body["consent_records"]) == 1
    assert set(body["available_consent_types"]) == {
        "marketing",
        "analytics",
        "profiling",
        "cookies",
    }


def test_preference_center_is_empty_for_a_new_user(privacy_client):
    response = privacy_client.get("/privacy/preferences", params={"user_id": str(uuid4())})

    assert response.status_code == 200
    assert response.json()["consent_records"] == []


def test_privacy_endpoints_require_a_user_id(privacy_client):
    assert privacy_client.get("/privacy/consent").status_code == 422
    assert privacy_client.get("/privacy/preferences").status_code == 422


def test_consent_repo_dependency_is_overridden_not_the_real_provider(privacy_client):
    """Sanity check on the test harness: the override is the one in use."""
    assert privacy_client.app.dependency_overrides[get_consent_repo] is not None


# ---------------------------------------------------------------------------
# dsar.py
# ---------------------------------------------------------------------------


def test_dsar_router_is_prefixed_dsar():
    assert dsar_router.prefix == "/dsar"


def test_create_dsar_returns_201_with_a_30_day_sla(dsar_client):
    user_id = uuid4()
    before = datetime.now(UTC)

    response = dsar_client.post(
        "/dsar",
        json={
            "user_id": str(user_id),
            "request_type": "access",
            "data_categories": ["profile", "devices"],
            "reason": "need my data",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert body["request_type"] == "access"
    assert body["status"] == "pending"
    # Categories are serialised to a single comma-joined column.
    assert body["data_categories"] == "profile,devices"
    sla = datetime.fromisoformat(body["sla_deadline"])
    if sla.tzinfo is None:
        sla = sla.replace(tzinfo=UTC)
    delta_days = (sla - before).days
    assert 29 <= delta_days <= 30


def test_create_dsar_defaults_categories_to_an_empty_json_array(dsar_client):
    response = dsar_client.post(
        "/dsar", json={"user_id": str(uuid4()), "request_type": "deletion"}
    )

    assert response.status_code == 201
    assert response.json()["data_categories"] == "[]"


def test_create_dsar_rejects_an_unknown_request_type(dsar_client):
    response = dsar_client.post(
        "/dsar", json={"user_id": str(uuid4()), "request_type": "obliterate"}
    )

    assert response.status_code == 422


def test_get_dsar_returns_the_created_request(dsar_client):
    created = dsar_client.post(
        "/dsar", json={"user_id": str(uuid4()), "request_type": "portability"}
    ).json()

    response = dsar_client.get(f"/dsar/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["request_type"] == "portability"


def test_get_dsar_404s_for_an_unknown_id(dsar_client):
    response = dsar_client.get(f"/dsar/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "DSAR not found"


def test_get_dsar_rejects_a_non_uuid_id(dsar_client):
    assert dsar_client.get("/dsar/not-a-uuid").status_code == 422
