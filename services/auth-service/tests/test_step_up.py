import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from app.core.settings import settings
from app.security import TokenManager, role_for_email
from app.schemas import StepUpRequest


@pytest.fixture
def admin_user_id():
    return uuid.uuid4()


@pytest.fixture
def admin_email():
    return "demo@wildframe.com"


def _make_access_token(uid, email="demo@wildframe.com", av=0):
    settings.ADMIN_EMAILS = email if "demo" in email else f"{email},demo@wildframe.com"
    if role_for_email(email) != "admin":
        settings.ADMIN_EMAILS = email
    return TokenManager.create_access_token(uid, email, av)


def _make_step_up_token(
    uid, email="demo@wildframe.com", av=0, amr=None, scope="admin:destructive", exp_minutes=5
):
    if amr is None:
        amr = ["pwd"]
    return TokenManager.create_admin_step_up_token(uid, email, av, amr, scope)


class TestTokenManagerStepUp:
    def test_create_admin_step_up_claims(self, admin_user_id, admin_email):
        settings.ADMIN_EMAILS = admin_email
        token = TokenManager.create_admin_step_up_token(
            admin_user_id, admin_email, 3, ["pwd", "mfa"]
        )
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        assert payload["type"] == "admin_step_up"
        assert payload["role"] == "admin"
        assert payload["sub"] == str(admin_user_id)
        assert payload["av"] == 3
        assert payload["arv"] == settings.ADMIN_ROLE_VERSION
        assert payload["amr"] == ["pwd", "mfa"]
        assert payload["scope"] == "admin:destructive"
        assert payload["aud"] == settings.JWT_AUDIENCE
        assert payload["iss"] == settings.JWT_ISSUER
        assert "jti" in payload
        assert payload["jti"].startswith("stepup_")
        assert payload["exp"] - payload["iat"] == 300

    def test_step_up_and_access_type_distinct(self, admin_user_id, admin_email):
        access = TokenManager.create_access_token(admin_user_id, admin_email, 0)
        step = TokenManager.create_admin_step_up_token(admin_user_id, admin_email, 0, ["pwd"])
        a_payload = jwt.decode(
            access,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        s_payload = jwt.decode(
            step,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        assert a_payload["type"] == "access"
        assert s_payload["type"] == "admin_step_up"
        assert a_payload["type"] != s_payload["type"]

    def test_jti_unique(self, admin_user_id, admin_email):
        t1 = TokenManager.create_admin_step_up_token(admin_user_id, admin_email, 0, ["pwd"])
        t2 = TokenManager.create_admin_step_up_token(admin_user_id, admin_email, 0, ["pwd"])
        p1 = jwt.decode(
            t1,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        p2 = jwt.decode(
            t2,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        assert p1["jti"] != p2["jti"]

    def test_verify_token_rejects_wrong_type(self, admin_user_id, admin_email):
        access = TokenManager.create_access_token(admin_user_id, admin_email, 0)
        try:
            result = TokenManager.verify_token(access, token_type="admin_step_up")
        except Exception as e:
            assert "Invalid token type" in str(e)
            return
        assert result is None

    def test_expired_step_up_rejected(self, admin_user_id, admin_email):
        now = datetime.now(UTC) - timedelta(minutes=10)
        exp = now + timedelta(minutes=5)
        payload = {
            "sub": str(admin_user_id),
            "user_id": str(admin_user_id),
            "email": admin_email,
            "role": "admin",
            "type": "admin_step_up",
            "av": 0,
            "arv": settings.ADMIN_ROLE_VERSION,
            "amr": ["pwd"],
            "scope": "admin:destructive",
            "iat": now,
            "exp": exp,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"stepup_{admin_user_id}_{now.timestamp()}_test",
        }
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        assert TokenManager.verify_token(token, token_type="admin_step_up") is None


@pytest.mark.asyncio
class TestStepUpEndpoint:
    async def _call_step_up(
        self,
        monkeypatch,
        user,
        password_ok=True,
        mfa_code_ok=True,
        allow_ok=True,
        request_password="secret12345678!",
        request_mfa=None,
    ):
        from app.api.routes.auth import step_up
        from fastapi import Request

        monkeypatch.setattr("app.api.routes.auth.allow", AsyncMock(return_value=allow_ok))
        mock_repo = MagicMock()
        mock_repo.get_by_id = AsyncMock(return_value=user)
        monkeypatch.setattr("app.api.routes.auth.UserRepository", lambda db: mock_repo)
        monkeypatch.setattr(
            "app.api.routes.auth.PasswordManager.verify_password", lambda a, b: password_ok
        )
        if user.mfa_enabled:
            monkeypatch.setattr(
                "app.api.routes.auth.SecretCipher.decrypt", lambda x: "JBSWY3DPEHPK3PXP"
            )
            import pyotp

            monkeypatch.setattr(
                pyotp.TOTP,
                "verify",
                lambda self, code, valid_window=1: mfa_code_ok and code == request_mfa,
            )

        fake_request = MagicMock(spec=Request)
        fake_request.client.host = "127.0.0.1"
        step_req = StepUpRequest(password=request_password, mfa_code=request_mfa)
        db = AsyncMock()
        result = await step_up(step_req, fake_request, user.id, db)
        return result, mock_repo

    async def test_step_up_success_without_mfa(self, monkeypatch, admin_user_id, admin_email):
        settings.ADMIN_EMAILS = admin_email
        user = MagicMock()
        user.id = admin_user_id
        user.email = admin_email
        user.auth_version = 0
        user.password_hash = "hashed"
        user.mfa_enabled = False
        user.mfa_secret = None
        result, _ = await self._call_step_up(
            monkeypatch, user, password_ok=True, allow_ok=True, request_password="goodpass12345"
        )
        assert "step_up_token" in result
        payload = jwt.decode(
            result["step_up_token"],
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        assert payload["type"] == "admin_step_up"
        assert "pwd" in payload["amr"]
        assert payload["scope"] == "admin:destructive"

    async def test_step_up_success_with_mfa(self, monkeypatch, admin_user_id, admin_email):
        settings.ADMIN_EMAILS = admin_email
        user = MagicMock()
        user.id = admin_user_id
        user.email = admin_email
        user.auth_version = 1
        user.password_hash = "hashed"
        user.mfa_enabled = True
        user.mfa_secret = "enc_secret"
        result, _ = await self._call_step_up(
            monkeypatch,
            user,
            password_ok=True,
            mfa_code_ok=True,
            allow_ok=True,
            request_password="goodpass12345",
            request_mfa="123456",
        )
        assert "step_up_token" in result
        payload = jwt.decode(
            result["step_up_token"],
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        assert "pwd" in payload["amr"]
        assert "mfa" in payload["amr"]

    async def test_step_up_rejects_wrong_password(self, monkeypatch, admin_user_id, admin_email):
        settings.ADMIN_EMAILS = admin_email
        user = MagicMock()
        user.id = admin_user_id
        user.email = admin_email
        user.auth_version = 0
        user.password_hash = "hashed"
        user.mfa_enabled = False
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await self._call_step_up(monkeypatch, user, password_ok=False, allow_ok=True)
        assert exc.value.status_code == 401

    async def test_step_up_requires_mfa_code_when_enabled(
        self, monkeypatch, admin_user_id, admin_email
    ):
        settings.ADMIN_EMAILS = admin_email
        user = MagicMock()
        user.id = admin_user_id
        user.email = admin_email
        user.auth_version = 0
        user.password_hash = "hashed"
        user.mfa_enabled = True
        user.mfa_secret = "enc"
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await self._call_step_up(
                monkeypatch, user, password_ok=True, allow_ok=True, request_mfa=None
            )
        assert exc.value.status_code == 400

    async def test_step_up_rejects_invalid_mfa(self, monkeypatch, admin_user_id, admin_email):
        settings.ADMIN_EMAILS = admin_email
        user = MagicMock()
        user.id = admin_user_id
        user.email = admin_email
        user.auth_version = 0
        user.password_hash = "hashed"
        user.mfa_enabled = True
        user.mfa_secret = "enc"
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await self._call_step_up(
                monkeypatch,
                user,
                password_ok=True,
                mfa_code_ok=False,
                allow_ok=True,
                request_mfa="000000",
            )
        assert exc.value.status_code == 401

    async def test_step_up_rate_limited(self, monkeypatch, admin_user_id, admin_email):
        settings.ADMIN_EMAILS = admin_email
        user = MagicMock()
        user.id = admin_user_id
        user.email = admin_email
        user.auth_version = 0
        user.password_hash = "hashed"
        user.mfa_enabled = False
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await self._call_step_up(monkeypatch, user, password_ok=True, allow_ok=False)
        assert exc.value.status_code == 429

    async def test_step_up_rejects_non_admin(self, monkeypatch, admin_user_id):
        non_admin_email = "user@example.com"
        settings.ADMIN_EMAILS = "demo@wildframe.com"
        user = MagicMock()
        user.id = admin_user_id
        user.email = non_admin_email
        user.auth_version = 0
        user.password_hash = "hashed"
        user.mfa_enabled = False
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await self._call_step_up(
                monkeypatch, user, password_ok=True, allow_ok=True, request_password="goodpass12345"
            )
        assert exc.value.status_code == 403
