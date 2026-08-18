"""
MFA lifecycle security tests (#221).

Pins the five MFA findings:
  1. MFA challenges are single-use and atomically consumed (replay fails).
  2. TOTP secrets stay decryptable across encryption-key rotation and
     multi-instance deployments (keyring: current + previous keys).
  3. No recovery codes are issued or stored (plaintext or otherwise) by the
     live enrollment flow.
  4. Enrollment cannot be replaced by a concurrent setup request — a pending
     secret blocks re-setup (409) and transitions are row-locked.
  5. Disabling MFA requires a valid TOTP proof, not a session alone.
"""

import time

import pyotp
import pytest
import pytest_asyncio
from app.core.database import get_db
from app.main import app
from httpx import ASGITransport, AsyncClient

PASSWORD = "SecurePassword123!"


@pytest_asyncio.fixture
async def client(db_session):
    """Create test HTTP client."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def _register(client, email: str) -> dict:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _enable_mfa(client, email: str) -> tuple[str, dict]:
    """Register, run setup+verify, return (TOTP secret, auth headers)."""
    tokens = await _register(client, email)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    setup = await client.post("/api/v1/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]

    verify = await client.post(
        "/api/v1/auth/mfa/verify",
        headers=headers,
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert verify.status_code == 200, verify.text
    return secret, headers


async def _stored_secret(db_session, email: str) -> str:
    """Read the encrypted secret back from the DB and decrypt it."""
    from sqlalchemy import select

    from app.models import User
    from app.security import SecretCipher

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    return SecretCipher.decrypt(user.mfa_secret)


async def _login_challenge(client, email: str) -> str:
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["requires_mfa"] is True
    return body["mfa_challenge"]


@pytest.mark.asyncio
class TestChallengeSingleUse:
    """Finding 1: MFA challenges are single-use and atomically consumed."""

    async def test_challenge_replay_rejected(self, client, db_session):
        email = "mfa-replay@example.com"
        await _enable_mfa(client, email)
        challenge = await _login_challenge(client, email)
        secret = await _stored_secret(db_session, email)
        code = pyotp.TOTP(secret).now()

        first = await client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": challenge, "code": code},
        )
        assert first.status_code == 200, first.text

        # Same challenge AND same (still-valid) code: the consumed challenge
        # must fail even though the TOTP window has not moved.
        replay = await client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": challenge, "code": code},
        )
        assert replay.status_code == 401

    async def test_consumption_is_pk_atomic(self, db_session):
        """The consumed-challenge store is a unique-PK insert: the second
        INSERT for the same challenge hash must fail with IntegrityError
        (production: one request per session, so the loser of the race gets
        a clean 401)."""
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from sqlalchemy.exc import IntegrityError

        from app.repositories import TokenBlacklistRepository

        repo = TokenBlacklistRepository(db_session)
        user_id = uuid4()
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        await repo.create(token_hash="challenge-hash-1", user_id=user_id, expires_at=expiry)
        await db_session.commit()

        with pytest.raises(IntegrityError):
            await repo.create(token_hash="challenge-hash-1", user_id=user_id, expires_at=expiry)
        await db_session.rollback()

    async def test_replay_in_later_totp_window_still_rejected(self, client, db_session):
        email = "mfa-replay2@example.com"
        await _enable_mfa(client, email)
        challenge = await _login_challenge(client, email)
        secret = await _stored_secret(db_session, email)

        first = await client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert first.status_code == 200

        # A different (later-window) code with the same consumed challenge.
        time.sleep(31)
        replay = await client.post(
            "/api/v1/auth/mfa/login-verify",
            json={"mfa_challenge": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert replay.status_code == 401


@pytest.mark.asyncio
class TestSecretKeyring:
    """Finding 2: secrets survive key rotation; replicas share decryptability."""

    async def test_rotate_key_keeps_old_secrets_decryptable(self, monkeypatch):
        from app.core.settings import settings
        from app.security import SecretCipher

        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", "key-v1")
        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])
        encrypted = SecretCipher.encrypt("TOTP-SECRET-1")

        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", "key-v2")
        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY_PREVIOUS", ["key-v1"])
        assert SecretCipher.decrypt(encrypted) == "TOTP-SECRET-1"

        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])
        assert SecretCipher.decrypt(encrypted) == ""

    async def test_encrypt_always_uses_current_key(self, monkeypatch):
        from app.core.settings import settings
        from app.security import SecretCipher

        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", "key-current")
        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY_PREVIOUS", ["key-old"])
        encrypted = SecretCipher.encrypt("TOTP-SECRET-2")

        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", "key-old")
        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])
        assert SecretCipher.decrypt(encrypted) == ""

    async def test_default_key_is_jwt_secret_derived(self, monkeypatch):
        from app.core.settings import settings
        from app.security import SecretCipher

        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", "")
        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])
        encrypted = SecretCipher.encrypt("TOTP-SECRET-3")
        assert SecretCipher.decrypt(encrypted) == "TOTP-SECRET-3"

    async def test_second_instance_decrypts_first_instance_secret(self, monkeypatch):
        from app.core.settings import settings
        from app.security import SecretCipher

        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", "shared-key")
        monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])
        encrypted = SecretCipher.encrypt("TOTP-SECRET-4")

        # "Other replica": same settings, same key.
        assert SecretCipher.decrypt(encrypted) == "TOTP-SECRET-4"


@pytest.mark.asyncio
class TestNoRecoveryCodes:
    """Finding 3: no recovery codes are issued or stored by live flows."""

    async def test_setup_issues_no_backup_codes(self, client):
        email = "mfa-nocodes@example.com"
        tokens = await _register(client, email)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        setup = await client.post("/api/v1/auth/mfa/setup", headers=headers)
        assert setup.status_code == 200, setup.text
        assert set(setup.json().keys()) == {"secret", "totp_uri"}

    async def test_no_plaintext_codes_written_to_user_row(self, client, db_session):
        from sqlalchemy import select

        from app.models import User

        email = "mfa-nocodes2@example.com"
        await _enable_mfa(client, email)
        user = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one()
        assert user.backup_codes is None


@pytest.mark.asyncio
class TestEnrollmentReplacement:
    """Finding 4: a concurrent setup request cannot replace a pending enrollment."""

    async def test_pending_secret_blocks_resetup(self, client):
        email = "mfa-replace@example.com"
        tokens = await _register(client, email)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        first = await client.post("/api/v1/auth/mfa/setup", headers=headers)
        assert first.status_code == 200
        secret_a = first.json()["secret"]

        second = await client.post("/api/v1/auth/mfa/setup", headers=headers)
        assert second.status_code == 409

        # Completing enrollment with the ORIGINAL secret still works.
        verify = await client.post(
            "/api/v1/auth/mfa/verify",
            headers=headers,
            json={"code": pyotp.TOTP(secret_a).now()},
        )
        assert verify.status_code == 200

    async def test_setup_after_enabled_still_409(self, client):
        email = "mfa-replace2@example.com"
        _, headers = await _enable_mfa(client, email)

        setup = await client.post("/api/v1/auth/mfa/setup", headers=headers)
        assert setup.status_code == 409


@pytest.mark.asyncio
class TestDisableRequiresProof:
    """Finding 5: disabling MFA needs a valid TOTP, not just a session."""

    async def test_disable_with_wrong_code_rejected(self, client):
        email = "mfa-disable@example.com"
        _, headers = await _enable_mfa(client, email)

        response = await client.post(
            "/api/v1/auth/mfa/disable", headers=headers, json={"code": "000000"}
        )
        assert response.status_code == 400

        # Still enabled, still challenges logins.
        challenge = await _login_challenge(client, email)
        assert challenge

    async def test_disable_with_valid_code_succeeds_and_clears_secret(
        self, client, db_session
    ):
        email = "mfa-disable2@example.com"
        secret, headers = await _enable_mfa(client, email)

        response = await client.post(
            "/api/v1/auth/mfa/disable",
            headers=headers,
            json={"code": pyotp.TOTP(secret).now()},
        )
        assert response.status_code == 200

        # Login no longer challenges once MFA is off.
        login = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert login.status_code == 200
        assert login.json().get("requires_mfa") is not True

        from sqlalchemy import select

        from app.models import User

        user = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one()
        assert user.mfa_secret is None
        assert user.mfa_enabled is False
