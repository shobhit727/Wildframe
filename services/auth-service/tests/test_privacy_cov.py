"""Auth privacy 80% coverage."""

from datetime import UTC, datetime
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient

from app.models.privacy import ConsentRecord, PrivacyNotice
from app.schemas import PrivacyNoticeCreate


def test_notice_version_validation():
    data = PrivacyNoticeCreate(
        version="2.1.3",
        jurisdiction="US",
        title="T",
        content="C",
        language="en",
        effective_date=datetime.now(UTC),
    )
    assert data.version == "2.1.3"


def test_notice_jurisdiction_in():
    n = PrivacyNotice(
        version="1.0.0",
        jurisdiction="IN",
        title="IN",
        content="C",
        language="en",
        effective_date=datetime.now(UTC),
    )
    assert n.jurisdiction == "IN"


def test_consent_granted_false():
    c = ConsentRecord(
        user_id=uuid4(),
        consent_type="cookies",
        jurisdiction="GLOBAL",
        granted=False,
        version="1.0.0",
    )
    assert c.granted is False


def test_consent_metadata():
    c = ConsentRecord(
        user_id=uuid4(),
        consent_type="profiling",
        jurisdiction="EU",
        granted=True,
        version="1.0.0",
        consent_metadata='{"a":1}',
    )
    assert "a" in c.consent_metadata


def test_privacy_notice_is_current():
    n = PrivacyNotice(
        version="1.0.0",
        jurisdiction="EU",
        title="T",
        content="C",
        language="en",
        effective_date=datetime.now(UTC),
        is_current=True,
    )
    assert n.is_current is True


# ==========================================================================
# Privacy API routes (app/api/routes/privacy.py)
#
# The router is mounted on the production app under /api/v1/privacy. These
# tests drive it through the real app with a throwaway SQLite database and a
# real admin bearer token, so the admin gate, the jurisdiction normaliser and
# every consent state transition are exercised end to end.
# ==========================================================================

import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from app.api.routes import auth as auth_routes
from app.api.routes.privacy import _validate_jurisdiction, require_admin
from app.core.database import DatabaseManager
from app.core.settings import settings
from app.main import create_app
from app.models import Base, ConsentRecord, PrivacyNotice
from app.repositories.privacy_repository import (
    ConsentRecordRepository,
    PrivacyNoticeRepository,
)
from app.security import PasswordManager, TokenManager


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_notice(session, version="1.0.0", jurisdiction="EU", language="en", **kwargs):
    fields = {
        "version": version,
        "jurisdiction": jurisdiction,
        "title": f"Notice {version}",
        "content": "content",
        "language": language,
        "effective_date": datetime.now(UTC),
    }
    fields.update(kwargs)
    notice = PrivacyNotice(**fields)
    session.add(notice)
    return notice


@pytest.fixture
def privacy_app(tmp_path):
    """A ``create_app()`` instance bound to a fresh SQLite database."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/privacy.db")

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
    return engine, factory


@pytest.fixture
def privacy_client(privacy_app):
    from app.core import event_consumer

    async def _consumer(_f):
        return None

    with patch.object(
        event_consumer, "run_user_moderation_consumer", _consumer
    ), patch("app.main.setup_logging"):
        with TestClient(create_app()) as client:
            yield client


@pytest.fixture
def session(privacy_app):
    """A direct session against the same database, for arranging fixtures."""
    _engine, factory = privacy_app
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        session = loop.run_until_complete(_enter_session(factory))
    finally:
        loop.close()
    yield session
    loop2 = asyncio.new_event_loop()
    try:
        loop2.run_until_complete(session.close())
    finally:
        loop2.close()


async def _enter_session(factory):
    return factory()


ADMIN_EMAIL = "privacy-admin@wildframe.com"
USER_EMAIL = "privacy-user@wildframe.com"


@pytest.fixture
def tokens(privacy_client, monkeypatch, privacy_app):
    """Register an admin and a normal user, returning their bearer tokens."""
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ADMIN_EMAIL)

    def _register(email):
        response = privacy_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123!",
                "first_name": "Privacy",
                "last_name": "Tester",
            },
        )
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return {"admin": _register(ADMIN_EMAIL), "user": _register(USER_EMAIL)}


def _auth(email: str) -> dict:
    return {"Authorization": f"Bearer {email}"}


class TestValidateJurisdiction:
    def test_known_jurisdiction_is_accepted(self):
        assert _validate_jurisdiction("EU") == "EU"
        assert _validate_jurisdiction("US-CA") == "US-CA"

    def test_unknown_jurisdiction_is_still_stored(self):
        assert _validate_jurisdiction("ZZ") == "ZZ"

    def test_unknown_jurisdiction_logs_a_warning(self, caplog):
        with caplog.at_level("WARNING", logger="app.api.routes.privacy"):
            _validate_jurisdiction("Mars")

        assert "Unknown jurisdiction requested: Mars" in caplog.text


class TestRequireAdmin:
    async def test_admin_email_is_allowed(self, test_session, monkeypatch):
        from app.models import User

        monkeypatch.setattr(settings, "ADMIN_EMAILS", ADMIN_EMAIL)
        user = User(
            email=ADMIN_EMAIL, password_hash=PasswordManager.hash_password("Secret1234!")
        )
        test_session.add(user)
        await test_session.commit()

        assert await require_admin(user.id, test_session) == user.id

    async def test_non_admin_is_403(self, test_session, monkeypatch):
        from fastapi import HTTPException

        from app.models import User

        monkeypatch.setattr(settings, "ADMIN_EMAILS", ADMIN_EMAIL)
        user = User(
            email=USER_EMAIL, password_hash=PasswordManager.hash_password("Secret1234!")
        )
        test_session.add(user)
        await test_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user.id, test_session)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Admin privileges required"

    async def test_missing_user_is_403(self, test_session, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(settings, "ADMIN_EMAILS", ADMIN_EMAIL)

        with pytest.raises(HTTPException) as exc_info:
            await require_admin(uuid4(), test_session)

        assert exc_info.value.status_code == 403


class TestPrivacyNoticesAdminRoutes:
    def test_create_notice_and_become_current(self, privacy_client, tokens):
        response = privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "EU Notice",
                "content": "We respect your data.",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        assert response.status_code == 201
        body = response.json()
        assert body["version"] == "1.0.0"
        assert body["jurisdiction"] == "EU"
        assert body["is_current"] is True
        assert UUID(body["id"])

    def test_duplicate_version_is_409(self, privacy_client, tokens):
        payload = {
            "version": "1.0.0",
            "jurisdiction": "EU",
            "title": "EU Notice",
            "content": "c",
            "language": "en",
            "effective_date": datetime.now(UTC).isoformat(),
        }
        assert (
            privacy_client.post("/api/v1/privacy/notices", json=payload, headers=tokens["admin"]).status_code
            == 201
        )

        duplicate = privacy_client.post(
            "/api/v1/privacy/notices", json=payload, headers=tokens["admin"]
        )

        assert duplicate.status_code == 409
        assert "already exists for EU/en" in duplicate.json()["detail"]

    def test_create_requires_admin(self, privacy_client, tokens):
        response = privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "2.0.0",
                "jurisdiction": "EU",
                "title": "T",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["user"],
        )

        assert response.status_code == 403

    def test_create_requires_authentication(self, privacy_client):
        response = privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "2.0.0",
                "jurisdiction": "EU",
                "title": "T",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
        )

        assert response.status_code == 401

    def test_second_notice_does_not_become_current_implicitly(self, privacy_client, tokens):
        first = {
            "version": "1.0.0",
            "jurisdiction": "US",
            "title": "US v1",
            "content": "c",
            "language": "en",
            "effective_date": datetime.now(UTC).isoformat(),
        }
        privacy_client.post("/api/v1/privacy/notices", json=first, headers=tokens["admin"])

        second = {**first, "version": "1.1.0", "title": "US v2"}
        created = privacy_client.post(
            "/api/v1/privacy/notices", json=second, headers=tokens["admin"]
        )

        assert created.status_code == 201
        assert created.json()["is_current"] is False
        current = privacy_client.get("/api/v1/privacy/notices/current")
        assert current.json()["US"]["version"] == "1.0.0"

    def test_list_notices_for_a_jurisdiction(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "IN",
                "title": "IN v1",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.get("/api/v1/privacy/notices", params={"jurisdiction": "IN"})

        assert response.status_code == 200
        assert [n["version"] for n in response.json()] == ["1.0.0"]

    def test_list_notices_without_filter_returns_only_current(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "CA",
                "title": "CA v1",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.get("/api/v1/privacy/notices")

        assert [n["jurisdiction"] for n in response.json()] == ["CA"]

    def test_list_all_current_groups_by_jurisdiction(self, privacy_client, tokens):
        for jurisdiction in ("EU", "US"):
            privacy_client.post(
                "/api/v1/privacy/notices",
                json={
                    "version": "1.0.0",
                    "jurisdiction": jurisdiction,
                    "title": f"{jurisdiction} v1",
                    "content": "c",
                    "language": "en",
                    "effective_date": datetime.now(UTC).isoformat(),
                },
                headers=tokens["admin"],
            )

        response = privacy_client.get("/api/v1/privacy/notices/current")

        assert set(response.json()) == {"EU", "US"}

    def test_get_specific_notice(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "3.2.1",
                "jurisdiction": "EU",
                "title": "EU v3",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.get("/api/v1/privacy/notices/3.2.1/EU/en")

        assert response.status_code == 200
        assert response.json()["title"] == "EU v3"

    def test_get_missing_notice_is_404(self, privacy_client, tokens):
        response = privacy_client.get("/api/v1/privacy/notices/9.9.9/EU/en")

        assert response.status_code == 404
        assert response.json()["detail"] == "Privacy notice not found: 9.9.9/EU/en"

    def test_update_notice_fields(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "old",
                "content": "old body",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.patch(
            "/api/v1/privacy/notices/1.0.0/EU/en",
            json={"title": "new", "content": "new body"},
            headers=tokens["admin"],
        )

        assert response.status_code == 200
        assert response.json()["title"] == "new"
        assert response.json()["content"] == "new body"

    def test_update_deprecated_date_and_metadata(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "t",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )
        deprecated = datetime.now(UTC) - timedelta(days=1)

        response = privacy_client.patch(
            "/api/v1/privacy/notices/1.0.0/EU/en",
            json={"deprecated_date": deprecated.isoformat(), "notice_metadata": '{"a":1}'},
            headers=tokens["admin"],
        )

        assert response.status_code == 200
        assert response.json()["deprecated_date"] is not None
        assert response.json()["notice_metadata"] == '{"a":1}'

    def test_update_can_demote_from_current(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "t",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.patch(
            "/api/v1/privacy/notices/1.0.0/EU/en",
            json={"is_current": False},
            headers=tokens["admin"],
        )

        assert response.status_code == 200
        assert response.json()["is_current"] is False

    def test_update_can_promote_to_current(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "t",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "2.0.0",
                "jurisdiction": "EU",
                "title": "t2",
                "content": "c2",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.patch(
            "/api/v1/privacy/notices/1.0.0/EU/en",
            json={"is_current": True},
            headers=tokens["admin"],
        )

        assert response.status_code == 200
        assert response.json()["is_current"] is True
        assert privacy_client.get("/api/v1/privacy/notices/current").json()["EU"]["version"] == "1.0.0"

    def test_update_missing_notice_is_404(self, privacy_client, tokens):
        response = privacy_client.patch(
            "/api/v1/privacy/notices/7.7.7/EU/en", json={"title": "x"}, headers=tokens["admin"]
        )

        assert response.status_code == 404

    def test_update_requires_admin(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "t",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.patch(
            "/api/v1/privacy/notices/1.0.0/EU/en", json={"title": "x"}, headers=tokens["user"]
        )

        assert response.status_code == 403

    def test_set_current_endpoint(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "t",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "2.0.0",
                "jurisdiction": "EU",
                "title": "t2",
                "content": "c2",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.post(
            "/api/v1/privacy/notices/1.0.0/EU/en/set-current", headers=tokens["admin"]
        )

        assert response.status_code == 200
        assert response.json()["is_current"] is True
        assert response.json()["deprecated_date"] is None
        assert privacy_client.get("/api/v1/privacy/notices/current").json()["EU"]["version"] == "1.0.0"

    def test_set_current_missing_notice_is_404(self, privacy_client, tokens):
        response = privacy_client.post(
            "/api/v1/privacy/notices/5.5.5/EU/en/set-current", headers=tokens["admin"]
        )

        assert response.status_code == 404

    def test_set_current_requires_admin(self, privacy_client, tokens):
        response = privacy_client.post(
            "/api/v1/privacy/notices/5.5.5/EU/en/set-current", headers=tokens["user"]
        )

        assert response.status_code == 403

    def test_deprecate_endpoint(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "t",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )

        response = privacy_client.post(
            "/api/v1/privacy/notices/1.0.0/EU/en/deprecate", headers=tokens["admin"]
        )

        assert response.status_code == 200
        assert response.json()["is_current"] is False
        assert response.json()["deprecated_date"] is not None
        assert privacy_client.get("/api/v1/privacy/notices/current").json() == {}

    def test_deprecate_missing_notice_is_404(self, privacy_client, tokens):
        response = privacy_client.post(
            "/api/v1/privacy/notices/4.4.4/EU/en/deprecate", headers=tokens["admin"]
        )

        assert response.status_code == 404

    def test_deprecate_requires_admin(self, privacy_client, tokens):
        response = privacy_client.post(
            "/api/v1/privacy/notices/4.4.4/EU/en/deprecate", headers=tokens["user"]
        )

        assert response.status_code == 403


def _consent_payload(user_id, **overrides):
    body = {
        "user_id": str(user_id),
        "consent_type": "marketing",
        "jurisdiction": "EU",
        "granted": True,
        "version": "1.0.0",
    }
    body.update(overrides)
    return body


def _user_id(token: str) -> UUID:
    return UUID(__import__("jose").jwt.get_unverified_claims(token)["user_id"])


class TestConsentRoutes:
    def test_create_consent_records_the_grant(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))

        response = privacy_client.post(
            "/api/v1/privacy/consent", json=_consent_payload(user_id)
        )

        assert response.status_code == 201
        body = response.json()
        assert body["granted"] is True
        assert body["granted_at"] is not None
        assert body["withdrawn_at"] is None
        assert body["consent_type"] == "marketing"

    def test_create_consent_with_granted_false_has_no_grant_time(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))

        response = privacy_client.post(
            "/api/v1/privacy/consent",
            json=_consent_payload(user_id, granted=False, consent_type="analytics"),
        )

        assert response.status_code == 201
        assert response.json()["granted"] is False
        assert response.json()["granted_at"] is None

    def test_duplicate_consent_is_409(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))

        duplicate = privacy_client.post(
            "/api/v1/privacy/consent", json=_consent_payload(user_id)
        )

        assert duplicate.status_code == 409
        assert "marketing" in duplicate.json()["detail"]
        assert "EU" in duplicate.json()["detail"]

    def test_list_user_consent(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))
        privacy_client.post(
            "/api/v1/privacy/consent",
            json=_consent_payload(user_id, consent_type="analytics"),
        )

        response = privacy_client.get(f"/api/v1/privacy/consent", params={"user_id": str(user_id)})

        assert response.status_code == 200
        assert {r["consent_type"] for r in response.json()} == {"marketing", "analytics"}

    def test_list_user_consent_for_unknown_user_is_empty(self, privacy_client):
        response = privacy_client.get(
            "/api/v1/privacy/consent", params={"user_id": str(uuid4())}
        )

        assert response.json() == []

    def test_active_consent_excludes_withdrawn(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))
        privacy_client.post(
            "/api/v1/privacy/consent", json=_consent_payload(user_id, consent_type="cookies")
        )
        withdrawn = privacy_client.patch(
            f"/api/v1/privacy/consent/{user_id}/cookies/EU", json={"granted": False}
        )
        assert withdrawn.status_code == 200

        response = privacy_client.get(
            "/api/v1/privacy/consent/active", params={"user_id": str(user_id)}
        )

        assert response.status_code == 200
        assert [r["consent_type"] for r in response.json()] == ["marketing"]

    def test_active_consent_is_empty_for_a_user_with_no_records(self, privacy_client):
        response = privacy_client.get(
            "/api/v1/privacy/consent/active", params={"user_id": str(uuid4())}
        )

        assert response.json() == []

    def test_get_specific_consent(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))

        response = privacy_client.get(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU"
        )

        assert response.status_code == 200
        assert response.json()["consent_type"] == "marketing"

    def test_get_missing_consent_is_404(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))

        response = privacy_client.get(f"/api/v1/privacy/consent/{user_id}/marketing/EU")

        assert response.status_code == 404
        assert "Consent record not found" in response.json()["detail"]

    def test_patch_consent_withdraws_granted_consent(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))

        response = privacy_client.patch(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU",
            json={"granted": False, "withdrawal_reason": "no longer interested"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["granted"] is False
        assert body["withdrawal_reason"] == "no longer interested"
        assert body["withdrawn_at"] is not None

    def test_patch_consent_regrants_withdrawn_consent(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))
        privacy_client.patch(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU", json={"granted": False}
        )

        response = privacy_client.patch(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU", json={"granted": True}
        )

        assert response.status_code == 200
        assert response.json()["granted"] is True
        assert response.json()["withdrawn_at"] is None
        assert response.json()["withdrawal_reason"] is None

    def test_patch_consent_updates_metadata_and_withdrawn_at(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))
        stamp = datetime.now(UTC) - timedelta(hours=1)

        response = privacy_client.patch(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU",
            json={
                "withdrawn_at": stamp.isoformat(),
                "withdrawal_reason": "manual",
                "consent_metadata": '{"source":"settings"}',
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["consent_metadata"] == '{"source":"settings"}'
        assert body["withdrawal_reason"] == "manual"
        assert body["withdrawn_at"] is not None

    def test_patch_consent_with_no_changes_is_a_noop(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))

        response = privacy_client.patch(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU", json={}
        )

        assert response.status_code == 200
        assert response.json()["granted"] is True

    def test_patch_missing_consent_is_404(self, privacy_client):
        response = privacy_client.patch(
            f"/api/v1/privacy/consent/{uuid4()}/marketing/EU", json={"granted": False}
        )

        assert response.status_code == 404

    def test_grant_endpoint_regrants_withdrawn_consent(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))
        privacy_client.patch(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU", json={"granted": False}
        )

        response = privacy_client.post(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU/grant"
        )

        assert response.status_code == 200
        assert response.json()["granted"] is True
        assert response.json()["withdrawn_at"] is None

    def test_grant_endpoint_409_when_already_granted(self, privacy_client, tokens):
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))

        response = privacy_client.post(
            f"/api/v1/privacy/consent/{user_id}/marketing/EU/grant"
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Consent is already granted"

    def test_grant_endpoint_404_for_unknown_record(self, privacy_client):
        response = privacy_client.post(f"/api/v1/privacy/consent/{uuid4()}/marketing/EU/grant")

        assert response.status_code == 404


class TestWithdrawConsentHelper:
    """``withdraw_consent`` is a plain coroutine (no route decorator)."""

    async def test_withdraws_a_granted_record(self, test_session):
        from app.api.routes.privacy import withdraw_consent

        record = ConsentRecord(
            user_id=uuid4(),
            consent_type="marketing",
            jurisdiction="EU",
            granted=True,
            version="1.0.0",
        )
        test_session.add(record)
        await test_session.commit()

        response = await withdraw_consent(
            record.user_id,
            "marketing",
            "EU",
            ConsentRecordRepository(test_session),
            test_session,
            "no longer needed",
        )
        await test_session.commit()

        assert response.granted is False
        assert response.withdrawal_reason == "no longer needed"
        assert response.withdrawn_at is not None

    async def test_404_for_unknown_record(self, test_session):
        from fastapi import HTTPException

        from app.api.routes.privacy import withdraw_consent

        with pytest.raises(HTTPException) as exc_info:
            await withdraw_consent(
                uuid4(),
                "marketing",
                "EU",
                ConsentRecordRepository(test_session),
                test_session,
            )

        assert exc_info.value.status_code == 404

    async def test_409_when_already_withdrawn(self, test_session):
        from fastapi import HTTPException

        from app.api.routes.privacy import withdraw_consent

        record = ConsentRecord(
            user_id=uuid4(),
            consent_type="marketing",
            jurisdiction="EU",
            granted=False,
            version="1.0.0",
        )
        test_session.add(record)
        await test_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await withdraw_consent(
                record.user_id,
                "marketing",
                "EU",
                ConsentRecordRepository(test_session),
                test_session,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Consent is already withdrawn"

    async def test_unknown_jurisdiction_is_still_looked_up(self, test_session):
        from fastapi import HTTPException

        from app.api.routes.privacy import withdraw_consent

        record = ConsentRecord(
            user_id=uuid4(),
            consent_type="marketing",
            jurisdiction="ZZ",
            granted=True,
            version="1.0.0",
        )
        test_session.add(record)
        await test_session.commit()

        response = await withdraw_consent(
            record.user_id,
            "marketing",
            "ZZ",
            ConsentRecordRepository(test_session),
            test_session,
        )
        await test_session.commit()

        assert response.jurisdiction == "ZZ"
        assert response.granted is False


class TestPreferenceCenter:
    def test_returns_notices_consent_and_the_catalogue(self, privacy_client, tokens):
        privacy_client.post(
            "/api/v1/privacy/notices",
            json={
                "version": "1.0.0",
                "jurisdiction": "EU",
                "title": "EU v1",
                "content": "c",
                "language": "en",
                "effective_date": datetime.now(UTC).isoformat(),
            },
            headers=tokens["admin"],
        )
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))

        response = privacy_client.get(
            "/api/v1/privacy/preferences", headers=tokens["user"]
        )

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == str(user_id)
        assert "EU" in body["current_notices"]
        assert [r["consent_type"] for r in body["consent_records"]] == ["marketing"]
        assert set(body["available_consent_types"]) == {
            "marketing",
            "analytics",
            "profiling",
            "third_party_sharing",
            "cookies",
            "location",
            "biometric",
        }
        assert "Marketing" in body["available_consent_types"]["marketing"]

    def test_requires_authentication(self, privacy_client):
        response = privacy_client.get("/api/v1/privacy/preferences")

        assert response.status_code == 401

    def test_only_the_caller_sees_their_own_consent(self, privacy_client, tokens, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_EMAILS", ADMIN_EMAIL)
        other = privacy_client.post(
            "/api/v1/auth/register",
            json={
                "email": "other@wildframe.com",
                "password": "SecurePass123!",
            },
        ).json()["access_token"]
        user_id = _user_id(tokens["user"]["Authorization"].removeprefix("Bearer "))
        privacy_client.post("/api/v1/privacy/consent", json=_consent_payload(user_id))

        response = privacy_client.get(
            "/api/v1/privacy/preferences", headers=_auth(other)
        )

        assert response.json()["consent_records"] == []
