"""Behavioural tests for ``app/api/routes/dsar_verify.py``.

Like ``age.py``, this router is **defined but never mounted** on the production
app (``app/api/routes/__init__.py`` only includes ``auth`` and ``privacy``), so
these tests mount it on a throwaway ``FastAPI()`` with a fake async session.

The endpoint is the DSAR identity gate: a caller must present a live access
token whose subject is the *same* user id they are asking about, otherwise the
request is refused. Both the missing-credential and the subject-mismatch
branches are covered here.
"""

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from app.api.routes.dsar_verify import router as dsar_router
from app.core.database import get_db
from app.core.settings import settings
from app.security import TokenManager
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _fake_session():
    session = MagicMock()
    session.flush = MagicMock()
    session.commit = MagicMock()
    session.refresh = MagicMock()
    session.execute = MagicMock()
    return session


@pytest.fixture
def dsar_app():
    app = FastAPI()
    app.include_router(dsar_router)
    return app


@pytest.fixture
def client_for(dsar_app):
    @contextlib.contextmanager
    def _build(session, **kwargs):
        dsar_app.dependency_overrides[get_db] = lambda: session
        try:
            with TestClient(dsar_app, **kwargs) as client:
                yield client
        finally:
            dsar_app.dependency_overrides.clear()

    return _build


def _payload(user_id, **overrides):
    body = {
        "user_id": str(user_id),
        "email": "dsar@example.com",
        "verification_method": "email_otp",
    }
    body.update(overrides)
    return body


def _bearer_for(user_id: uuid.UUID) -> str:
    return f"Bearer {TokenManager.create_access_token(user_id, 'dsar@example.com', 0)}"


class TestVerifyDsarIdentity:
    def test_matching_token_and_user_is_verified(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": _bearer_for(user_id)},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == str(user_id)
        assert body["verified"] is True
        assert body["method"] == "email_otp"
        assert body["verified_at"] is not None
        assert body["expires_at"] is not None

    def test_verified_window_is_exactly_24_hours(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": _bearer_for(user_id)},
            )

        verified_at = datetime.fromisoformat(response.json()["verified_at"])
        expires_at = datetime.fromisoformat(response.json()["expires_at"])
        assert expires_at - verified_at == timedelta(hours=24)
        assert verified_at.tzinfo is not None
        assert expires_at.utcoffset() == timedelta(0)

    def test_verification_never_touches_the_database(self, client_for):
        """The dev flow is a pure token check — nothing is persisted."""
        user_id = uuid.uuid4()
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": _bearer_for(user_id)},
            )

        assert response.status_code == 200
        session.add.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.parametrize("method", ["email_otp", "id_document", "knowledge"])
    def test_every_documented_verification_method_is_accepted(
        self, client_for, method
    ):
        user_id = uuid.uuid4()
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id, verification_method=method),
                headers={"Authorization": _bearer_for(user_id)},
            )

        assert response.status_code == 200
        assert response.json()["method"] == method

    def test_optional_otp_token_is_accepted(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id, token="123456"),
                headers={"Authorization": _bearer_for(user_id)},
            )

        assert response.status_code == 200
        assert response.json()["verified"] is True

    def test_missing_authorization_header_is_401(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
            response = client.post("/dsar/verify", json=_payload(uuid.uuid4()))

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing auth"

    def test_non_bearer_authorization_is_401(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(uuid.uuid4()),
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
            )

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing auth"

    def test_empty_bearer_prefix_is_401(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(uuid.uuid4()),
                headers={"Authorization": "Bearer "},
            )

        # "Bearer " strips to an empty token, which fails verification.
        assert response.status_code in (401, 403)

    def test_garbage_token_is_refused(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(uuid.uuid4()),
                headers={"Authorization": "Bearer not.a.jwt"},
            )

        assert response.status_code in (401, 403)
        assert response.json()["detail"] in {"Missing auth", "User mismatch"}

    def test_token_for_a_different_user_is_403(self, client_for):
        """The DSAR gate: possession of *a* valid token is not enough."""
        token_user = uuid.uuid4()
        claimed_user = uuid.uuid4()
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(claimed_user),
                headers={"Authorization": _bearer_for(token_user)},
            )

        assert response.status_code == 403
        assert response.json()["detail"] == "User mismatch"

    def test_refresh_token_produces_a_500_instead_of_a_401(self, client_for):
        """BUG: a non-access token makes this route raise instead of refuse.

        ``TokenManager.verify_token`` deliberately re-raises ``JWTError`` when
        the claim type does not match (``app/security/__init__.py:289-290``), and
        every other call site wraps it (``auth.get_current_user``,
        ``auth.verify_email``, ``auth.logout``). ``dsar_verify.py:32`` does not,
        so presenting a refresh token to ``POST /dsar/verify`` returns 500
        rather than 401. Asserted as-is; not fixed here.
        """
        user_id = uuid.uuid4()
        refresh_token = TokenManager.create_refresh_token(user_id)
        session = _fake_session()

        with client_for(session, raise_server_exceptions=False) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": f"Bearer {refresh_token}"},
            )

        assert response.status_code == 500

    def test_mfa_challenge_token_produces_a_500_instead_of_a_401(self, client_for):
        """Same uncaught-JWTError path as the refresh token above."""
        user_id = uuid.uuid4()
        challenge = TokenManager.create_mfa_challenge_token(user_id, "dsar@example.com")
        session = _fake_session()

        with client_for(session, raise_server_exceptions=False) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": f"Bearer {challenge}"},
            )

        assert response.status_code == 500

    def test_admin_step_up_token_produces_a_500_instead_of_a_401(self, client_for):
        user_id = uuid.uuid4()
        step_up = TokenManager.create_admin_step_up_token(
            user_id, "dsar@example.com", 0, ["pwd"]
        )
        session = _fake_session()

        with client_for(session, raise_server_exceptions=False) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": f"Bearer {step_up}"},
            )

        assert response.status_code == 500

    def test_wrong_audience_token_is_refused_with_403(self, client_for):
        """A wrong-audience token decodes to None, so the subject check fails.

        Unlike a type mismatch, ``JWTError("Invalid audience")`` is swallowed by
        ``TokenManager.verify_token`` and becomes ``None`` -> 403.
        """
        from jose import jwt

        user_id = uuid.uuid4()
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(user_id),
                "user_id": str(user_id),
                "type": "access",
                "iss": settings.JWT_ISSUER,
                "aud": "some-other-api",
                "iat": now,
                "exp": now + timedelta(minutes=15),
            },
            _signing_pem(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 403
        assert response.json()["detail"] == "User mismatch"

    def test_expired_access_token_is_refused(self, client_for):
        user_id = uuid.uuid4()
        expired = _expired_access_token(user_id, "dsar@example.com")
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id),
                headers={"Authorization": f"Bearer {expired}"},
            )

        assert response.status_code in (401, 403)

    def test_invalid_verification_method_is_rejected_by_the_schema(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify",
                json=_payload(user_id, verification_method="vibes"),
                headers={"Authorization": _bearer_for(user_id)},
            )

        assert response.status_code == 422

    def test_missing_user_id_is_rejected_by_the_schema(self, client_for):
        user_id = uuid.uuid4()
        body = _payload(user_id)
        body.pop("user_id")
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify", json=body, headers={"Authorization": _bearer_for(user_id)}
            )

        assert response.status_code == 422

    def test_missing_email_is_rejected_by_the_schema(self, client_for):
        user_id = uuid.uuid4()
        body = _payload(user_id)
        body.pop("email")
        session = _fake_session()

        with client_for(session) as client:
            response = client.post(
                "/dsar/verify", json=body, headers={"Authorization": _bearer_for(user_id)}
            )

        assert response.status_code == 422


def _signing_pem() -> str:
    from app.security.jwks import get_private_key_pem

    return get_private_key_pem()


def _expired_access_token(user_id: uuid.UUID, email: str) -> str:
    from jose import jwt

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": email,
            "type": "access",
            "av": 0,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        _signing_pem(),
        algorithm=settings.JWT_ALGORITHM,
        headers={"kid": settings.JWT_KEY_ID},
    )


class TestRouterShape:
    def test_router_is_prefixed_and_tagged(self):
        assert dsar_router.prefix == "/dsar"
        assert dsar_router.tags == ["dsar-verify"]

    def test_verify_route_is_declared(self):
        paths = {(route.path, tuple(sorted(route.methods))) for route in dsar_router.routes}
        assert ("/dsar/verify", ("POST",)) in paths

    def test_router_is_not_mounted_on_the_production_app(self):
        """Documents why these tests mount a throwaway app instead."""
        from app.api.routes import router as mounted_router
        from app.main import create_app

        mounted = set(create_app().openapi()["paths"])
        assert not any(path.startswith("/api/v1/dsar") for path in mounted)
        assert not any(
            getattr(r, "path", "").startswith("/dsar") for r in mounted_router.routes
        )
