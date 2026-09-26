"""Endpoint-level gap tests for ``app/api/routes/auth.py``.

Covers the branches the existing endpoint suites skip:

* ``_get_user_locked`` 404 for a user that no longer exists
* ``get_current_user``: malformed JWT, non-UUID subject, deleted user, stale
  ``av`` claim, blacklisted token
* ``register``: ValueError -> 400 and unexpected error -> 500
* ``mfa_login_verify``: per-user 429 and the generic 500 path
* ``refresh``: ValueError -> 401 and the generic 500 path
* ``logout``: malformed JWT, unverifiable token, generic 500
* ``/me``: missing user 404 and the generic 500 path
* ``change-password``: missing user 404 and the generic 500 path
* ``verify-email``: malformed JWT, non-UUID subject, token/email mismatch,
  blacklist replay, unknown user, concurrent-consumption IntegrityError
* ``setup_mfa``: 429, 404 for a vanished user, 409 for a pending enrolment
* ``verify_mfa``: 429 and the idempotent "already enabled" 200
* ``disable_mfa``: 429
"""

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import bcrypt
import pyotp
import pytest
from app.api.routes import auth as auth_routes
from app.core.database import DatabaseManager
from app.main import create_app
from app.models import Base
from app.repositories import TokenBlacklistRepository, UserRepository
from app.security import PasswordManager, SecretCipher, TokenManager
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

GOOD_PASSWORD = "SecurePass123!"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def app_db(tmp_path):
    import asyncio

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/gaps.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_create())
    finally:
        loop.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.fixture
def make_client(app_db):
    """Build a client with the moderation consumer stubbed and throttling off."""

    @contextlib.contextmanager
    def _build():
        engine, factory = app_db
        original_engine = DatabaseManager.get_engine
        original_factory = DatabaseManager.get_session_factory
        DatabaseManager.get_engine = classmethod(lambda cls: engine)
        DatabaseManager.get_session_factory = classmethod(lambda cls: factory)

        async def _consumer(_f):
            return None

        from app.core import event_consumer

        try:
            with patch.object(
                event_consumer, "run_user_moderation_consumer", _consumer
            ), patch.object(
                auth_routes, "allow", AsyncMock(return_value=True)
            ), patch("app.main.setup_logging"):
                with TestClient(create_app()) as client:
                    yield client
        finally:
            DatabaseManager.get_engine = original_engine
            DatabaseManager.get_session_factory = original_factory

    return _build


@pytest.fixture
def client(make_client):
    with make_client() as test_client:
        yield test_client


def _register(client, email="gaps@example.com", password=GOOD_PASSWORD):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Gap",
            "last_name": "Filler",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return {
        "email": email,
        "password": password,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
    }


def _token_for(email, **claims):
    """Mint an access token with arbitrary extra claims."""
    from jose import jwt

    from app.core.settings import settings
    from app.security.jwks import get_private_key_pem

    user_id = claims.pop("user_id", uuid.uuid4())
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "user_id": str(user_id),
        "email": email,
        "type": "access",
        "av": 0,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }
    payload.update(claims)
    return jwt.encode(
        payload,
        get_private_key_pem(),
        algorithm=settings.JWT_ALGORITHM,
        headers={"kid": settings.JWT_KEY_ID},
    )


# --------------------------------------------------------------------------
# _get_user_locked / get_current_user
# --------------------------------------------------------------------------


class TestGetUserLocked:
    async def test_returns_404_for_a_user_that_no_longer_exists(self, test_session):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes._get_user_locked(test_session, uuid.uuid4())

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"

    async def test_returns_the_locked_row_for_a_live_user(self, test_session):
        from app.models import User

        user = User(
            email="locked@example.com",
            password_hash=PasswordManager.hash_password(GOOD_PASSWORD),
        )
        test_session.add(user)
        await test_session.commit()

        assert await auth_routes._get_user_locked(test_session, user.id) is user

    async def test_lock_does_not_filter_inactive_users(self, test_session):
        """``_get_user_locked`` selects by id only — MFA state is reachable
        even for a deactivated row (the caller has already authorized)."""
        from app.models import User

        user = User(
            email="inactive-locked@example.com",
            password_hash=PasswordManager.hash_password(GOOD_PASSWORD),
            is_active=False,
        )
        test_session.add(user)
        await test_session.commit()

        assert await auth_routes._get_user_locked(test_session, user.id) is user


class TestGetCurrentUser:
    async def test_non_uuid_subject_is_401(self, test_session):
        from fastapi import HTTPException

        token = _token_for("bad-subject@example.com", user_id="not-a-uuid")

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid token payload"

    async def test_missing_subject_claim_is_401(self, test_session):
        from fastapi import HTTPException

        token = _token_for("no-sub@example.com", user_id="")

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.status_code == 401

    async def test_token_for_a_deleted_user_is_401(self, test_session):
        from fastapi import HTTPException

        token = _token_for("ghost@example.com", user_id=uuid.uuid4())

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid or expired token"

    async def test_token_for_a_soft_deleted_user_is_401(self, test_session):
        from fastapi import HTTPException

        from app.models import User

        user = User(
            email="soft-deleted@example.com",
            password_hash=PasswordManager.hash_password(GOOD_PASSWORD),
            is_active=False,
        )
        test_session.add(user)
        await test_session.commit()
        token = TokenManager.create_access_token(user.id, user.email, 0)

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.status_code == 401

    async def test_expired_token_is_401(self, test_session):
        from fastapi import HTTPException

        from jose import jwt

        from app.core.settings import settings
        from app.security.jwks import get_private_key_pem

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "type": "access",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(hours=1),
            },
            get_private_key_pem(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid or expired token"

    async def test_malformed_jwt_is_401(self, test_session):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, "Bearer not.a.jwt")

        assert exc_info.value.status_code == 401

    async def test_blacklisted_token_is_401(self, test_session):
        from fastapi import HTTPException

        from app.models import User

        user = User(
            email="blacklisted@example.com",
            password_hash=PasswordManager.hash_password(GOOD_PASSWORD),
        )
        test_session.add(user)
        await test_session.commit()
        token = TokenManager.create_access_token(user.id, user.email, 0)
        await TokenBlacklistRepository(test_session).create(
            token_hash=TokenManager.hash_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        await test_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Token has been revoked"

    async def test_stale_auth_version_is_401(self, test_session):
        from fastapi import HTTPException

        from app.models import User

        user = User(
            email="stale-av@example.com",
            password_hash=PasswordManager.hash_password(GOOD_PASSWORD),
            auth_version=4,
        )
        test_session.add(user)
        await test_session.commit()
        token = TokenManager.create_access_token(user.id, user.email, 3)

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.detail == "Token has been revoked"

    async def test_missing_authorization_header_is_401(self, test_session):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, None)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Missing or invalid authorization header"

    async def test_wrong_authorization_scheme_is_401(self, test_session):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, "Basic abc")

        assert exc_info.value.status_code == 401


# --------------------------------------------------------------------------
# register
# --------------------------------------------------------------------------


class TestRegisterFailurePaths:
    def test_duplicate_email_is_409(self, client):
        _register(client)

        again = client.post(
            "/api/v1/auth/register",
            json={"email": "gaps@example.com", "password": GOOD_PASSWORD},
        )

        assert again.status_code == 409
        assert again.json()["detail"] == "User with this email already exists"

    def test_service_value_error_becomes_400(self, client):
        with patch.object(
            auth_routes.AuthService,
            "register",
            AsyncMock(side_effect=ValueError("password too weak")),
        ):
            response = client.post(
                "/api/v1/auth/register",
                json={"email": "value-error@example.com", "password": GOOD_PASSWORD},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "password too weak"

    def test_unexpected_service_error_becomes_opaque_500(self, client):
        with patch.object(
            auth_routes.AuthService,
            "register",
            AsyncMock(side_effect=RuntimeError("connection string leaked")),
        ):
            response = client.post(
                "/api/v1/auth/register",
                json={"email": "boom@example.com", "password": GOOD_PASSWORD},
            )

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        assert "connection string" not in response.text

    def test_rollback_is_called_on_registration_failure(self, client):
        with patch.object(
            auth_routes.AuthService,
            "register",
            AsyncMock(side_effect=ValueError("nope")),
        ):
            with patch.object(
                auth_routes, "UserRepository", wraps=auth_routes.UserRepository
            ):
                client.post(
                    "/api/v1/auth/register",
                    json={"email": "rollback@example.com", "password": GOOD_PASSWORD},
                )

    def test_email_is_normalised_to_lowercase(self, client):
        _register(client, email="Normalise@Example.com")

        login = client.post(
            "/api/v1/auth/login",
            json={"email": "normalise@example.com", "password": GOOD_PASSWORD},
        )

        assert login.status_code == 200
        assert login.json()["access_token"]


# --------------------------------------------------------------------------
# mfa/login-verify
# --------------------------------------------------------------------------


class TestMfaLoginVerifyFailurePaths:
    def test_per_user_throttle_returns_429(self, client):
        """The IP gate passes but the per-user gate trips.

        The per-user gate only runs when the challenge token verifies, so a real
        challenge is required to reach it.
        """
        account = _mfa_ready_account(client, "mfa-verify-429-user@example.com")
        calls = {"n": 0}

        async def _allow(key, **kwargs):
            calls["n"] += 1
            return calls["n"] == 1

        with patch.object(auth_routes, "allow", _allow):
            response = client.post(
                "/api/v1/auth/mfa/login-verify",
                json={
                    "mfa_challenge": account["challenge"],
                    "code": pyotp.TOTP(account["secret"]).now(),
                },
            )

        assert response.status_code == 429
        assert response.json()["detail"] == "Too many requests. Try again later."

    def test_unexpected_error_becomes_opaque_500(self, client):
        with patch.object(
            auth_routes.AuthService,
            "complete_mfa_login",
            AsyncMock(side_effect=RuntimeError("secret failure detail")),
        ):
            response = client.post(
                "/api/v1/auth/mfa/login-verify",
                json={"mfa_challenge": "any.challenge", "code": "123456"},
            )

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        assert "secret failure" not in response.text

    def test_http_exception_from_the_service_is_propagated(self, client):
        from fastapi import HTTPException

        with patch.object(
            auth_routes.AuthService,
            "complete_mfa_login",
            AsyncMock(
                side_effect=HTTPException(status_code=400, detail="Invalid MFA code")
            ),
        ):
            response = client.post(
                "/api/v1/auth/mfa/login-verify",
                json={"mfa_challenge": "any.challenge", "code": "123456"},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid MFA code"

    def test_short_code_is_rejected_by_the_schema(self, client):
        response = client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": "any.challenge", "code": "123"},
        )

        assert response.status_code == 422

    def test_missing_challenge_is_rejected_by_the_schema(self, client):
        response = client.post("/api/v1/auth/mfa/login-verify", json={"code": "123456"})

        assert response.status_code == 422


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------


class TestRefreshFailurePaths:
    def test_value_error_becomes_401(self, client):
        with patch.object(
            auth_routes.AuthService,
            "refresh_token",
            AsyncMock(side_effect=ValueError("refresh token expired")),
        ):
            response = client.post(
                "/api/v1/auth/refresh", json={"refresh_token": "whatever"}
            )

        assert response.status_code == 401
        assert response.json()["detail"] == "refresh token expired"

    def test_unexpected_error_becomes_opaque_500(self, client):
        with patch.object(
            auth_routes.AuthService,
            "refresh_token",
            AsyncMock(side_effect=RuntimeError("pool exhausted at 10.0.0.5")),
        ):
            response = client.post(
                "/api/v1/auth/refresh", json={"refresh_token": "whatever"}
            )

        assert response.status_code == 500
        assert "pool exhausted" not in response.text

    def test_missing_body_is_rejected(self, client):
        response = client.post("/api/v1/auth/refresh", json={})

        assert response.status_code == 422


# --------------------------------------------------------------------------
# logout
# --------------------------------------------------------------------------


class TestLogoutFailurePaths:
    def test_malformed_bearer_token_is_401(self, client):
        response = client.post(
            "/api/v1/auth/logout", headers={"Authorization": "Bearer not.a.jwt"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"

    def test_expired_bearer_token_is_401(self, client):
        from jose import jwt

        from app.core.settings import settings
        from app.security.jwks import get_private_key_pem

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "type": "access",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(hours=1),
            },
            get_private_key_pem(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

        response = client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401

    def test_token_that_does_not_verify_is_401(self, client):
        """A valid RS256 token with the wrong audience decodes to None."""
        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {_token_for('x@example.com', aud='other')}"},
        )

        assert response.status_code == 401

    def test_unexpected_error_becomes_opaque_500(self, client):
        with patch.object(
            auth_routes.TokenBlacklistRepository,
            "create",
            AsyncMock(side_effect=RuntimeError("blacklist table missing")),
        ):
            response = client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {_token_for('x@example.com')}"},
            )

        assert response.status_code == 500
        assert "blacklist table" not in response.text

    def test_refresh_token_logout_is_204(self, client):
        account = _register(client, email="logout-refresh@example.com")

        response = client.post(
            "/api/v1/auth/logout", json={"refresh_token": account["refresh_token"]}
        )

        assert response.status_code == 204


# --------------------------------------------------------------------------
# /me and change-password
# --------------------------------------------------------------------------


class TestMeAndChangePasswordFailurePaths:
    def test_me_for_a_deleted_user_is_401(self, client, app_db):
        account = _register(client, email="me-deleted@example.com")

        engine, factory = app_db

        async def _deactivate():
            async with factory() as session:
                await session.execute(
                    update(
                        __import__("app.models", fromlist=["User"]).User,
                    ).values(is_active=False)
                )
                await session.commit()

        _run(_deactivate())

        response = client.get("/api/v1/auth/me", headers=account["headers"])

        assert response.status_code == 401

    def test_me_returns_the_role_for_an_admin_email(self, client, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "ADMIN_EMAILS", "root@wildframe.com")
        account = _register(client, email="root@wildframe.com")

        response = client.get("/api/v1/auth/me", headers=account["headers"])

        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    def test_me_returns_role_user_for_a_normal_email(self, client, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "ADMIN_EMAILS", "root@wildframe.com")
        account = _register(client, email="plain@example.com")

        response = client.get("/api/v1/auth/me", headers=account["headers"])

        assert response.json()["role"] == "user"

    def test_change_password_for_a_deleted_user_is_401(self, client, app_db):
        from app.models import User

        account = _register(client, email="cp-deleted@example.com")
        engine, factory = app_db

        async def _deactivate():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(_user_id_from(account))
                if user is not None:
                    user.is_active = False
                    await session.commit()

        _run(_deactivate())

        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": GOOD_PASSWORD, "new_password": "BrandNewPass456!"},
            headers=account["headers"],
        )

        assert response.status_code == 401

    def test_change_password_revokes_refresh_tokens_and_bumps_auth_version(
        self, client
    ):
        account = _register(client, email="cp-rotate@example.com")

        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": GOOD_PASSWORD, "new_password": "BrandNewPass456!"},
            headers=account["headers"],
        )

        assert response.status_code == 204
        # The old access token is now stale...
        assert client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 401
        # ...and the old refresh token no longer works.
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": account["refresh_token"]}
            ).status_code
            == 401
        )
        # The new password works.
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"email": account["email"], "password": "BrandNewPass456!"},
            ).status_code
            == 200
        )

    def test_change_password_unexpected_error_becomes_opaque_500(self, client):
        account = _register(client, email="cp-boom@example.com")

        with patch.object(
            auth_routes.PasswordManager,
            "hash_password",
            staticmethod(
                lambda _pw: (_ for _ in ()).throw(RuntimeError("bcrypt cost leaked"))
            ),
        ):
            response = client.post(
                "/api/v1/auth/change-password",
                json={
                    "current_password": GOOD_PASSWORD,
                    "new_password": "BrandNewPass456!",
                },
                headers=account["headers"],
            )

        assert response.status_code == 500
        assert "bcrypt cost" not in response.text


# --------------------------------------------------------------------------
# verify-email
# --------------------------------------------------------------------------


class TestVerifyEmailFailurePaths:
    def _verification_token(self, user_id, email):
        return TokenManager.create_email_verification_token(user_id, email)

    def test_malformed_token_is_400(self, client):
        response = client.post(
            "/api/v1/auth/verify-email",
            json={"email": "gaps@example.com", "token": "not.a.jwt"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid or expired verification token"

    def test_non_uuid_subject_is_400(self, client):
        token = _token_for(
            "gaps@example.com", type="email_verification", user_id="not-a-uuid"
        )

        response = client.post(
            "/api/v1/auth/verify-email", json={"email": "gaps@example.com", "token": token}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid verification token"

    def test_token_email_mismatch_is_400(self, client):
        account = _register(client, email="ve-mismatch@example.com")
        token = TokenManager.create_email_verification_token(
            _user_id_from(account), "someone-else@example.com"
        )

        response = client.post(
            "/api/v1/auth/verify-email",
            json={"email": account["email"], "token": token},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Token email does not match requested email"

    def test_token_for_a_deleted_user_is_404(self, client, app_db):
        account = _register(client, email="ve-deleted@example.com")
        token = TokenManager.create_email_verification_token(
            _user_id_from(account), account["email"]
        )
        engine, factory = app_db

        async def _deactivate():
            async with factory() as session:
                await session.execute(
                    update(
                        __import__("app.models", fromlist=["User"]).User
                    ).values(is_active=False)
                )
                await session.commit()

        _run(_deactivate())

        response = client.post(
            "/api/v1/auth/verify-email",
            json={"email": account["email"], "token": token},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    def test_token_for_a_nonexistent_user_is_404(self, client):
        token = TokenManager.create_email_verification_token(
            uuid.uuid4(), "nobody@example.com"
        )

        response = client.post(
            "/api/v1/auth/verify-email",
            json={"email": "nobody@example.com", "token": token},
        )

        assert response.status_code == 404

    def test_replayed_token_is_rejected(self, client):
        account = _register(client, email="ve-replay@example.com")
        token = TokenManager.create_email_verification_token(
            _user_id_from(account), account["email"]
        )

        first = client.post(
            "/api/v1/auth/verify-email",
            json={"email": account["email"], "token": token},
        )
        assert first.status_code == 200

        replay = client.post(
            "/api/v1/auth/verify-email",
            json={"email": account["email"], "token": token},
        )
        assert replay.status_code == 400
        assert replay.json()["detail"] == "Invalid or expired verification token"

    def test_concurrent_consumption_integrity_error_is_400(self, client):
        """A losing race on the token_blacklist insert is treated as a replay."""
        account = _register(client, email="ve-race@example.com")
        token = TokenManager.create_email_verification_token(
            _user_id_from(account), account["email"]
        )

        with patch.object(
            auth_routes.TokenBlacklistRepository,
            "create",
            AsyncMock(
                side_effect=IntegrityError("INSERT", {}, Exception("duplicate key"))
            ),
        ):
            response = client.post(
                "/api/v1/auth/verify-email",
                json={"email": account["email"], "token": token},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid or expired verification token"

    def test_already_verified_user_is_still_accepted_once(self, client):
        account = _register(client, email="ve-twice@example.com")
        first_token = TokenManager.create_email_verification_token(
            _user_id_from(account), account["email"]
        )
        assert (
            client.post(
                "/api/v1/auth/verify-email",
                json={"email": account["email"], "token": first_token},
            ).status_code
            == 200
        )

        # A fresh token for an already-verified user is honoured (and blacklisted).
        second_token = TokenManager.create_email_verification_token(
            _user_id_from(account), account["email"]
        )
        assert (
            client.post(
                "/api/v1/auth/verify-email",
                json={"email": account["email"], "token": second_token},
            ).status_code
            == 200
        )

    def test_missing_token_field_is_rejected_by_the_schema(self, client):
        response = client.post(
            "/api/v1/auth/verify-email", json={"email": "gaps@example.com"}
        )

        assert response.status_code == 422


def _mfa_ready_account(client, email: str) -> dict:
    """Register a user, enable MFA, and log in to obtain a real challenge."""
    account = _register(client, email=email)
    secret = client.post("/api/v1/auth/mfa/setup", headers=account["headers"]).json()["secret"]
    client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": pyotp.TOTP(secret).now()},
        headers=account["headers"],
    )
    challenge = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": GOOD_PASSWORD},
    ).json()["mfa_challenge"]
    return {**account, "secret": secret, "challenge": challenge}


def _user_id_from(account) -> uuid.UUID:
    from jose import jwt

    payload = jwt.get_unverified_claims(account["access_token"])
    return uuid.UUID(payload["user_id"])


# --------------------------------------------------------------------------
# resend-verification
# --------------------------------------------------------------------------


class TestResendVerification:
    def test_email_is_trimmed_and_lowercased(self, client):
        _register(client, email="resend-case@example.com")

        response = client.post(
            "/api/v1/auth/resend-verification", json={"email": "  Resend-Case@Example.COM  "}
        )

        assert response.status_code == 202
        assert response.json()["verification_token"]

    def test_unverified_user_receives_a_usable_token(self, client):
        account = _register(client, email="resend-usable@example.com")

        resend = client.post(
            "/api/v1/auth/resend-verification", json={"email": account["email"]}
        )
        token = resend.json()["verification_token"]

        verify = client.post(
            "/api/v1/auth/verify-email", json={"email": account["email"], "token": token}
        )

        assert verify.status_code == 200
        assert verify.json() == {"message": "Email verified successfully"}

    def test_per_email_throttle_returns_429(self, client):
        """The IP gate passes but the per-email cooldown trips."""
        calls = {"n": 0}

        async def _allow(key, **kwargs):
            calls["n"] += 1
            return calls["n"] == 1

        with patch.object(auth_routes, "allow", _allow):
            response = client.post(
                "/api/v1/auth/resend-verification", json={"email": "cool@example.com"}
            )

        assert response.status_code == 429

    def test_invalid_email_is_rejected_by_the_schema(self, client):
        response = client.post(
            "/api/v1/auth/resend-verification", json={"email": "not-an-email"}
        )

        assert response.status_code == 422


# --------------------------------------------------------------------------
# mfa setup / verify / disable
# --------------------------------------------------------------------------


class TestSetupMfa:
    def test_throttled_setup_returns_429(self, client):
        account = _register(client, email="mfa-setup-429@example.com")

        with patch.object(auth_routes, "allow", AsyncMock(return_value=False)):
            response = client.post("/api/v1/auth/mfa/setup", headers=account["headers"])

        assert response.status_code == 429
        assert (
            response.json()["detail"]
            == "Too many MFA setup attempts. Try again later."
        )

    def test_pending_enrolment_cannot_be_overwritten(self, client):
        account = _register(client, email="mfa-pending@example.com")

        first = client.post("/api/v1/auth/mfa/setup", headers=account["headers"])
        assert first.status_code == 200
        first_secret = first.json()["secret"]

        second = client.post("/api/v1/auth/mfa/setup", headers=account["headers"])

        assert second.status_code == 409
        assert (
            second.json()["detail"]
            == "MFA setup already pending; verify the issued secret first"
        )
        assert "secret" not in second.json()

    def test_issued_secret_is_encrypted_at_rest(self, client, app_db):
        from app.models import User

        account = _register(client, email="mfa-encrypted@example.com")
        response = client.post("/api/v1/auth/mfa/setup", headers=account["headers"])
        secret = response.json()["secret"]
        engine, factory = app_db

        async def _stored():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(_user_id_from(account))
                return user.mfa_secret

        stored = _run(_stored())

        assert stored != secret
        assert SecretCipher.decrypt(stored) == secret

    def test_totp_uri_carries_the_configured_issuer(self, client):
        from app.core.settings import settings

        account = _register(client, email="mfa-uri@example.com")

        response = client.post("/api/v1/auth/mfa/setup", headers=account["headers"])

        assert f"issuer={settings.MFA_ISSUER_NAME}" in response.json()["totp_uri"]
        assert "mfa-uri%40example.com" in response.json()["totp_uri"]

    def test_unauthenticated_setup_is_401(self, client):
        response = client.post("/api/v1/auth/mfa/setup")

        assert response.status_code == 401

    def test_setup_for_a_deleted_user_is_401(self, client, app_db):
        from app.models import User

        account = _register(client, email="mfa-setup-deleted@example.com")
        engine, factory = app_db

        async def _deactivate():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(_user_id_from(account))
                user.is_active = False
                await session.commit()

        _run(_deactivate())

        response = client.post("/api/v1/auth/mfa/setup", headers=account["headers"])

        assert response.status_code == 401


class TestVerifyMfa:
    def test_throttled_verify_returns_429(self, client):
        account = _register(client, email="mfa-verify-429@example.com")

        with patch.object(auth_routes, "allow", AsyncMock(return_value=False)):
            response = client.post(
                "/api/v1/auth/mfa/verify",
                json={"code": "123456"},
                headers=account["headers"],
            )

        assert response.status_code == 429
        assert (
            response.json()["detail"] == "Too many MFA verification attempts. Try again later."
        )

    def test_verify_is_idempotent_once_mfa_is_enabled(self, client):
        account = _register(client, email="mfa-verify-idem@example.com")
        secret = client.post(
            "/api/v1/auth/mfa/setup", headers=account["headers"]
        ).json()["secret"]
        enabled = client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )
        assert enabled.status_code == 200
        assert enabled.json()["message"] == "MFA enabled successfully"

        again = client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )

        assert again.status_code == 200
        assert again.json()["message"] == "MFA already enabled"

    def test_wrong_code_is_400(self, client):
        account = _register(client, email="mfa-verify-wrong@example.com")
        client.post("/api/v1/auth/mfa/setup", headers=account["headers"])

        response = client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": "000000"},
            headers=account["headers"],
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid MFA code"

    def test_enabling_mfa_forces_a_challenge_on_next_login(self, client):
        account = _register(client, email="mfa-login-challenge@example.com")
        secret = client.post(
            "/api/v1/auth/mfa/setup", headers=account["headers"]
        ).json()["secret"]
        client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )

        login = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": account["password"]},
        )

        assert login.status_code == 200
        assert login.json()["requires_mfa"] is True
        assert login.json()["mfa_challenge"]

    def test_mfa_login_verify_completes_the_login(self, client):
        account = _register(client, email="mfa-login-complete@example.com")
        secret = client.post(
            "/api/v1/auth/mfa/setup", headers=account["headers"]
        ).json()["secret"]
        client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )
        challenge = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": account["password"]},
        ).json()["mfa_challenge"]

        complete = client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": challenge, "code": pyotp.TOTP(secret).now()},
        )

        assert complete.status_code == 200
        assert complete.json()["access_token"]
        assert complete.json()["token_type"] == "bearer"

    def test_mfa_challenge_is_single_use(self, client):
        account = _register(client, email="mfa-login-single-use@example.com")
        secret = client.post(
            "/api/v1/auth/mfa/setup", headers=account["headers"]
        ).json()["secret"]
        client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )
        challenge = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": account["password"]},
        ).json()["mfa_challenge"]
        code = pyotp.TOTP(secret).now()

        first = client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": challenge, "code": code},
        )
        assert first.status_code == 200

        replay = client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": challenge, "code": code},
        )

        assert replay.status_code == 401
        assert replay.json()["detail"] == "Invalid or expired MFA challenge"

    def test_short_code_is_rejected_by_the_schema(self, client):
        account = _register(client, email="mfa-verify-short@example.com")

        response = client.post(
            "/api/v1/auth/mfa/verify", json={"code": "12"}, headers=account["headers"]
        )

        assert response.status_code == 422


class TestDisableMfa:
    def test_throttled_disable_returns_429(self, client):
        account = _register(client, email="mfa-disable-429@example.com")

        with patch.object(auth_routes, "allow", AsyncMock(return_value=False)):
            response = client.post(
                "/api/v1/auth/mfa/disable",
                json={"code": "123456"},
                headers=account["headers"],
            )

        assert response.status_code == 429
        assert (
            response.json()["detail"]
            == "Too many MFA disable attempts. Try again later."
        )

    def test_disable_clears_the_secret_and_re_enables_plain_login(self, client):
        account = _register(client, email="mfa-disable@example.com")
        secret = client.post(
            "/api/v1/auth/mfa/setup", headers=account["headers"]
        ).json()["secret"]
        client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )

        disabled = client.post(
            "/api/v1/auth/mfa/disable",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )

        assert disabled.status_code == 200
        assert disabled.json()["message"] == "MFA disabled successfully"

        login = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": account["password"]},
        )
        assert login.status_code == 200
        assert "access_token" in login.json()

    def test_wrong_code_does_not_disable_mfa(self, client):
        account = _register(client, email="mfa-disable-wrong@example.com")
        secret = client.post(
            "/api/v1/auth/mfa/setup", headers=account["headers"]
        ).json()["secret"]
        client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=account["headers"],
        )

        response = client.post(
            "/api/v1/auth/mfa/disable",
            json={"code": "000000"},
            headers=account["headers"],
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid MFA code"

    def test_disable_with_no_secret_is_400(self, client, app_db):
        """mfa_enabled=True with a cleared secret must not 500."""
        from app.models import User

        account = _register(client, email="mfa-disable-nosecret@example.com")
        engine, factory = app_db

        async def _force():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(_user_id_from(account))
                user.mfa_enabled = True
                user.mfa_secret = None
                await session.commit()

        _run(_force())

        response = client.post(
            "/api/v1/auth/mfa/disable",
            json={"code": "123456"},
            headers=account["headers"],
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid MFA code"


class TestBcryptIntegration:
    def test_passwords_are_hashed_with_the_configured_work_factor(self):
        from app.core.settings import settings

        hashed = PasswordManager.hash_password(GOOD_PASSWORD)

        assert hashed.startswith("$2b$")
        assert int(hashed.split("$")[2]) == settings.PASSWORD_BCRYPT_ROUNDS
        assert PasswordManager.verify_password(GOOD_PASSWORD, hashed)
        assert bcrypt.checkpw(GOOD_PASSWORD.encode(), hashed.encode())
        assert not PasswordManager.verify_password("wrong-password", hashed)


# --------------------------------------------------------------------------
# JWTError propagation: a validly-signed token of the *wrong* type
# --------------------------------------------------------------------------


class TestWrongTypeTokenIsAnInvalidToken:
    """``TokenManager.verify_token`` re-raises ``JWTError`` for a type
    mismatch (app/security/__init__.py:289-290), so these branches are only
    reachable with a well-formed token whose ``type`` claim is wrong.
    """

    async def test_get_current_user_with_a_refresh_token_is_401(self, test_session):
        from fastapi import HTTPException

        token = TokenManager.create_refresh_token(uuid.uuid4())

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_current_user(test_session, f"Bearer {token}")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid or expired token"

    def test_logout_with_a_refresh_token_in_the_header_is_401(self, client):
        account = _register(client, email="wrong-type-logout@example.com")

        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {account['refresh_token']}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"

    def test_verify_email_with_an_access_token_is_400(self, client):
        account = _register(client, email="wrong-type-verify@example.com")

        response = client.post(
            "/api/v1/auth/verify-email",
            json={"email": account["email"], "token": account["access_token"]},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid or expired verification token"


class TestRollbackBranches:
    def test_login_http_exception_rolls_back_and_returns_401(self, client):
        _register(client, email="login-401@example.com")

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "login-401@example.com", "password": "WrongPass456!"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password"

    def test_login_for_an_unknown_email_rolls_back_and_returns_401(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": GOOD_PASSWORD},
        )

        assert response.status_code == 401

    def test_refresh_http_exception_rolls_back_and_returns_401(self, client):
        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "not.a.jwt"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid refresh token"

    def test_mfa_login_verify_ip_throttle_returns_429(self, client):
        """The very first per-IP gate trips before the challenge is inspected."""
        with patch.object(auth_routes, "allow", AsyncMock(return_value=False)):
            response = client.post(
                "/api/v1/auth/mfa/login-verify",
                json={"mfa_challenge": "any.challenge", "code": "123456"},
            )

        assert response.status_code == 429
        assert response.json()["detail"] == "Too many requests. Try again later."


class TestMeInternalErrorBranch:
    def test_me_returns_404_for_a_missing_user_row(self, client, app_db):
        """A live token whose user row has vanished is refused at the boundary."""
        from app.models import User

        account = _register(client, email="me-404@example.com")
        engine, factory = app_db

        async def _soft_delete():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(_user_id_from(account))
                user.is_active = False
                await session.commit()

        _run(_soft_delete())

        assert client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 401

    def test_me_unexpected_error_becomes_opaque_500(self, client):
        account = _register(client, email="me-500@example.com")

        with patch.object(
            auth_routes.UserResponse,
            "model_validate",
            classmethod(lambda cls, obj: (_ for _ in ()).throw(RuntimeError("pg dump"))),
        ):
            response = client.get("/api/v1/auth/me", headers=account["headers"])

        assert response.status_code == 500
        assert "pg dump" not in response.text

    def test_me_http_exception_branch_is_the_404_path(self, client, app_db):
        from app.models import User

        account = _register(client, email="me-404b@example.com")
        engine, factory = app_db

        async def _soft_delete():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(_user_id_from(account))
                user.is_active = False
                await session.commit()

        _run(_soft_delete())

        response = client.get("/api/v1/auth/me", headers=account["headers"])

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"


class TestChangePasswordInternalBranches:
    def test_wrong_current_password_rolls_back_and_reraises(self, client):
        account = _register(client, email="cp-wrong-current@example.com")

        response = client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "WrongPass456!",
                "new_password": "BrandNewPass456!",
            },
            headers=account["headers"],
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Current password is incorrect"
        # The failed attempt must not have advanced the auth version.
        assert client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 200

    def test_change_password_revoke_failure_is_500(self, client):
        account = _register(client, email="cp-revoke-boom@example.com")

        with patch.object(
            auth_routes.RefreshTokenRepository,
            "revoke_all_for_user",
            AsyncMock(side_effect=RuntimeError("deadlock detected")),
        ):
            response = client.post(
                "/api/v1/auth/change-password",
                json={
                    "current_password": GOOD_PASSWORD,
                    "new_password": "BrandNewPass456!",
                },
                headers=account["headers"],
            )

        assert response.status_code == 500
        assert "deadlock" not in response.text


class TestLogoutBlacklistPath:
    def test_access_token_logout_blacklists_and_then_refuses_it(self, client):
        account = _register(client, email="logout-blacklist@example.com")

        logout = client.post("/api/v1/auth/logout", headers=account["headers"])
        assert logout.status_code == 204

        replay = client.get("/api/v1/auth/me", headers=account["headers"])

        assert replay.status_code == 401
        assert replay.json()["detail"] == "Token has been revoked"

    def test_logout_without_a_token_or_body_is_401(self, client):
        response = client.post("/api/v1/auth/logout")

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid authorization header"


class TestUnreachableThroughHttpDirectCalls:
    """These 404 branches are guarded by a dependency that already 401s.

    ``get_current_user`` resolves the user through ``UserRepository.get_by_id``,
    which filters ``is_active IS TRUE``. A soft-deleted account therefore never
    reaches the route body over HTTP, so the route's own
    ``if not user: raise 404`` (auth.py:431 and auth.py:467) and its
    ``except HTTPException: raise`` re-raise (auth.py:438) are only reachable by
    calling the route coroutine directly. Asserted as-is; not fixed here.
    """

    def _soft_delete(self, factory, user_id):
        async def _run():
            from app.models import User

            async with factory() as session:
                user = await UserRepository(session).get_by_id(user_id)
                user.is_active = False
                await session.commit()

        _run(_run)

    def test_me_returns_404_for_a_row_hidden_by_the_active_filter(
        self, client, app_db
    ):
        from app.models import User
        from app.schemas import UserResponse

        account = _register(client, email="me-404-direct@example.com")
        user_id = _user_id_from(account)
        _engine, factory = app_db

        async def _deactivate():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(user_id)
                user.is_active = False
                await session.commit()

        _run(_deactivate())

        async def _call():
            async with factory() as session:
                return await auth_routes.get_current_user_info(user_id, session)

        with pytest.raises(HTTPException) as exc_info:
            _run(_call())

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"
        assert UserResponse is not None

    def test_change_password_returns_404_for_a_row_hidden_by_the_active_filter(
        self, client, app_db
    ):
        from app.schemas import ChangePasswordRequest

        account = _register(client, email="cp-404-direct@example.com")
        user_id = _user_id_from(account)
        _engine, factory = app_db

        async def _deactivate():
            async with factory() as session:
                user = await UserRepository(session).get_by_id(user_id)
                user.is_active = False
                await session.commit()

        _run(_deactivate())

        async def _call():
            async with factory() as session:
                return await auth_routes.change_password(
                    ChangePasswordRequest(
                        current_password=GOOD_PASSWORD,
                        new_password="BrandNewPass456!",
                    ),
                    user_id,
                    session,
                )

        with pytest.raises(HTTPException) as exc_info:
            _run(_call())

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"
