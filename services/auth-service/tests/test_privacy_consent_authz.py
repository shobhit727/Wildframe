"""Authorization tests for the consent routes under ``/api/v1/privacy/consent`` (#1136).

Every consent route must (1) require a verified bearer token, (2) let a user act
only on their own ``user_id``, and (3) let an admin act on another user's
consent. The tests drive the real app with real tokens against a throwaway
SQLite database, so they exercise the production dependency wiring rather than
mocks.
"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import patch

from app.core.database import DatabaseManager
from app.core.settings import settings
from app.main import create_app
from app.models import Base

ADMIN_EMAIL = "consent-admin@wildframe.com"
OWNER_EMAIL = "consent-owner@wildframe.com"
OTHER_EMAIL = "consent-other@wildframe.com"

BASE = "/api/v1/privacy/consent"


@pytest.fixture
def consent_client(tmp_path):
    """The production app bound to a fresh SQLite database."""
    import asyncio

    from app.core import event_consumer

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/consent.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_create())
    finally:
        loop.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    DatabaseManager.get_engine = classmethod(lambda cls: engine)
    DatabaseManager.get_session_factory = classmethod(lambda cls: factory)

    async def _consumer(_f):
        return None

    with (
        patch.object(event_consumer, "run_user_moderation_consumer", _consumer),
        patch("app.main.setup_logging"),
    ):
        with TestClient(create_app()) as client:
            yield client


class Actor:
    def __init__(self, user_id: UUID, headers: dict[str, str]):
        self.id = user_id
        self.headers = headers


@pytest.fixture
def actors(consent_client, monkeypatch):
    """Register an admin, an owner (user A) and another user (user B)."""
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ADMIN_EMAIL)

    def _register(email: str) -> Actor:
        response = consent_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123!",
                "first_name": "Consent",
                "last_name": "Tester",
            },
        )
        assert response.status_code == 201, response.text
        token = response.json()["access_token"]
        user_id = UUID(jwt.get_unverified_claims(token)["user_id"])
        return Actor(user_id, {"Authorization": f"Bearer {token}"})

    return {
        "admin": _register(ADMIN_EMAIL),
        "owner": _register(OWNER_EMAIL),
        "other": _register(OTHER_EMAIL),
    }


def _payload(user_id: UUID, **overrides) -> dict:
    body = {
        "user_id": str(user_id),
        "consent_type": "marketing",
        "jurisdiction": "EU",
        "granted": True,
        "version": "1.0.0",
    }
    body.update(overrides)
    return body


# (id, method, path template, json body factory, seeded record state, owner status)
# ``seed`` is None for create (no pre-existing record), otherwise the ``granted``
# value of the record seeded for the target user before the call.
CASES = [
    pytest.param("POST", BASE, "create", None, 201, id="create"),
    pytest.param("GET", BASE + "?user_id={uid}", None, True, 200, id="list"),
    pytest.param("GET", BASE + "/active?user_id={uid}", None, True, 200, id="active"),
    pytest.param("GET", BASE + "/{uid}/marketing/EU", None, True, 200, id="get"),
    pytest.param("PATCH", BASE + "/{uid}/marketing/EU", {"granted": False}, True, 200, id="patch"),
    pytest.param("POST", BASE + "/{uid}/marketing/EU/grant", None, False, 200, id="grant"),
]


def _call(client, method, path, body, uid, headers):
    url = path.format(uid=uid)
    if body == "create":
        body = _payload(uid)
    return client.request(method, url, json=body, headers=headers)


def _seed(client, target: Actor, granted: bool):
    """Create a consent record for ``target`` as ``target`` (legitimate self-service)."""
    response = client.post(BASE, json=_payload(target.id, granted=granted), headers=target.headers)
    assert response.status_code == 201, response.text


def _records(client, target: Actor) -> list[dict]:
    response = client.get(f"{BASE}?user_id={target.id}", headers=target.headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(("method", "path", "body", "seed", "ok_status"), CASES)
class TestConsentRouteAuthz:
    def test_unauthenticated_is_401(
        self, consent_client, actors, method, path, body, seed, ok_status
    ):
        target = actors["owner"]
        if seed is not None:
            _seed(consent_client, target, seed)

        response = _call(consent_client, method, path, body, target.id, headers={})

        assert response.status_code == 401, response.text

    def test_invalid_token_is_401(
        self, consent_client, actors, method, path, body, seed, ok_status
    ):
        target = actors["owner"]
        if seed is not None:
            _seed(consent_client, target, seed)

        response = _call(
            consent_client,
            method,
            path,
            body,
            target.id,
            headers={"Authorization": "Bearer not-a-real-token"},
        )

        assert response.status_code == 401, response.text

    def test_other_user_is_403_and_nothing_changes(
        self, consent_client, actors, method, path, body, seed, ok_status
    ):
        victim, attacker = actors["other"], actors["owner"]
        if seed is not None:
            _seed(consent_client, victim, seed)
        before = _records(consent_client, victim)

        response = _call(consent_client, method, path, body, victim.id, attacker.headers)

        assert response.status_code == 403, response.text
        assert _records(consent_client, victim) == before

    def test_owner_succeeds(self, consent_client, actors, method, path, body, seed, ok_status):
        owner = actors["owner"]
        if seed is not None:
            _seed(consent_client, owner, seed)

        response = _call(consent_client, method, path, body, owner.id, owner.headers)

        assert response.status_code == ok_status, response.text

    def test_admin_can_act_on_another_user(
        self, consent_client, actors, method, path, body, seed, ok_status
    ):
        target, admin = actors["other"], actors["admin"]
        if seed is not None:
            _seed(consent_client, target, seed)

        response = _call(consent_client, method, path, body, target.id, admin.headers)

        assert response.status_code == ok_status, response.text


class TestCreateConsentBodyUserId:
    def test_mismatched_body_user_id_is_rejected_not_overridden(self, consent_client, actors):
        attacker, victim = actors["owner"], actors["other"]

        response = consent_client.post(BASE, json=_payload(victim.id), headers=attacker.headers)

        assert response.status_code == 403, response.text
        assert _records(consent_client, victim) == []
        assert _records(consent_client, attacker) == []

    def test_unknown_user_id_in_body_is_rejected(self, consent_client, actors):
        owner = actors["owner"]

        response = consent_client.post(BASE, json=_payload(uuid4()), headers=owner.headers)

        assert response.status_code == 403, response.text

    def test_admin_create_for_another_user_is_stored_against_that_user(
        self, consent_client, actors
    ):
        admin, target = actors["admin"], actors["other"]

        response = consent_client.post(BASE, json=_payload(target.id), headers=admin.headers)

        assert response.status_code == 201, response.text
        assert response.json()["user_id"] == str(target.id)
        assert len(_records(consent_client, target)) == 1
        assert _records(consent_client, admin) == []


class TestOtherUserCannotProbeForExistence:
    def test_missing_record_for_another_user_is_403_not_404(self, consent_client, actors):
        """A non-admin gets the same 403 whether or not the victim has a record."""
        attacker, victim = actors["owner"], actors["other"]

        response = consent_client.get(f"{BASE}/{victim.id}/marketing/EU", headers=attacker.headers)

        assert response.status_code == 403, response.text

    def test_random_user_id_is_403_for_non_admin(self, consent_client, actors):
        response = consent_client.get(f"{BASE}?user_id={uuid4()}", headers=actors["owner"].headers)

        assert response.status_code == 403, response.text
