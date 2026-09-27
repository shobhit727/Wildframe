"""Token-type separation and subject resolution on the pipeline auth boundary.

``app/api/media_pipeline_routes.py::get_current_user_id`` is the only thing
standing between an anonymous caller and "start a transcode job". Every test in
``test_routes.py`` *overrode* that dependency away, so the branches below were
never executed by the suite. They are exercised here through the real ASGI
stack (no dependency override on the auth path) with genuinely signed tokens:

* a refresh token is refused when presented as an access token. Refresh tokens
  carry the same ``aud``/``iss`` as access tokens, so only the explicit ``type``
  check can reject them (#221).
* the ``user_id`` claim fallback, the missing-subject rejection, and the happy
  path that resolves ``sub`` to a ``UUID``.

Tokens are signed RS256 with the runtime-generated key from ``_test_jwks`` and
the outbound JWKS fetch is stubbed, so the SDK verifier still does real
signature checking rather than being mocked out.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app.api.media_pipeline_routes import get_current_user_id, get_pipeline_service
from app.core.settings import settings
from app.main import app
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch):
    """Replace the JWKS *fetch* seam only; verification itself stays real."""

    async def fetch(_url):
        return JWKS

    monkeypatch.setattr("wildframe_auth.verifier.fetch_jwks", fetch)
    clear_jwks_cache()
    yield
    clear_jwks_cache()


def mint_token(**claims) -> str:
    """Sign a token that satisfies every check in ``get_current_user_id``.

    ``aud``/``iss``/``iat``/``av``/``exp`` are supplied so the *only* thing left
    to reject a token is the claim under test.
    """
    now = datetime.now(UTC)
    payload = {
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "iat": now,
        "av": 0,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(claims)
    return jose_jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def service():
    """A pipeline service stub — this module is about auth, not the state machine."""
    from unittest.mock import AsyncMock, MagicMock

    stub = MagicMock()
    stub.start_job = AsyncMock()
    stub.advance = AsyncMock()
    stub.job_repo = MagicMock(get=AsyncMock(return_value=None))
    stub.log_repo = MagicMock(list_for_job=AsyncMock(return_value=[]))
    return stub


@pytest.fixture
def client(service):
    """Client with the *auth* dependency left intact and only the service stubbed."""
    app.dependency_overrides.clear()
    app.dependency_overrides[get_pipeline_service] = lambda: service
    try:
        yield TestClient(app, base_url="http://localhost")
    finally:
        app.dependency_overrides.clear()


def _start(client, token: str):
    return client.post(
        f"/api/v1/pipeline/jobs/{uuid4()}/start",
        json={
            "content_id": str(uuid4()),
            "upload_session_id": str(uuid4()),
            "storage_key": "uploads/x/v.mp4",
            "idempotency_key": f"test-{uuid4()}",
        },
        headers={"Authorization": f"Bearer {token}"},
    )


class TestAccessTokenSubjectResolution:
    async def test_a_valid_access_token_resolves_to_its_sub(self):
        """Positive control: the rejections below are not passing by accident."""
        subject = uuid4()
        token = mint_token(sub=str(subject), type="access")
        assert await get_current_user_id(f"Bearer {token}") == subject

    async def test_a_token_carrying_user_id_instead_of_sub_is_refused(self):
        """The legacy ``user_id``-only claim no longer authenticates.

        Under the deleted shared-secret path such a token was accepted and
        ``sub or user_id`` supplied the identity. The SDK verifier requires
        ``sub`` as a claim, so it is refused rather than silently reinterpreting
        a different claim as the caller's identity.
        """
        token = mint_token(user_id=str(uuid4()), type="access")
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Invalid token"

    async def test_sub_wins_when_both_subject_claims_are_present(self):
        sub, user_id = uuid4(), uuid4()
        token = mint_token(sub=str(sub), user_id=str(user_id), type="access")
        assert await get_current_user_id(f"Bearer {token}") == sub

    async def test_a_token_with_no_subject_claim_is_refused(self):
        token = mint_token(type="access")
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Invalid token"


class TestRefreshTokenCannotBeUsedAsAnAccessToken:
    """Token-type separation (#221) — now enforced by the shared verifier."""

    async def test_a_refresh_token_is_refused(self):
        token = mint_token(sub=str(uuid4()), type="refresh")
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Invalid token"

    async def test_the_refresh_token_is_otherwise_fully_valid(self):
        """Prove the rejection comes from the type check and not from a decode failure.

        A refresh token minted here is correctly signed with the published key,
        unexpired, and carries the exact audience/issuer an access token needs.
        It fails verification *only* on ``type`` -- so if the expected_type
        check were removed this token would authenticate, which is the
        vulnerability.
        """
        from wildframe_auth.verifier import verify_token

        token = mint_token(sub=str(uuid4()), type="refresh")
        claims = verify_token(
            token,
            JWKS,
            settings.JWT_AUDIENCE,
            settings.JWT_ISSUER,
            expected_type="refresh",
        )
        assert claims["type"] == "refresh"
        assert claims["aud"] == settings.JWT_AUDIENCE
        assert claims["iss"] == settings.JWT_ISSUER
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401
        assert getattr(excinfo.value, "detail", None) == "Invalid token"

    @pytest.mark.parametrize("token_type", ["refresh", "logout", "", None])
    async def test_any_non_access_type_is_refused(self, token_type):
        token = mint_token(sub=str(uuid4()), type=token_type)
        with pytest.raises(Exception) as excinfo:
            await get_current_user_id(f"Bearer {token}")
        assert getattr(excinfo.value, "status_code", None) == 401


class TestOverHttp:
    """The same rules as a real caller sees them, through the real router."""

    async def test_start_with_a_refresh_token_is_401(self, client, service):
        job = type("J", (), {})()
        service.start_job.return_value = job
        response = _start(client, mint_token(sub=str(uuid4()), type="refresh"))
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"
        # The refusal must happen before any work is kicked off.
        assert service.start_job.await_count == 0

    async def test_get_job_with_a_refresh_token_is_401(self, client):
        response = client.get(
            f"/api/v1/pipeline/jobs/{uuid4()}",
            headers={"Authorization": f"Bearer {mint_token(sub=str(uuid4()), type='refresh')}"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    async def test_start_with_a_subjectless_token_is_401(self, client, service):
        response = _start(client, mint_token(type="access"))
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"
        assert service.start_job.await_count == 0
