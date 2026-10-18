"""The media-pipeline token boundary, after it was moved onto the SDK verifier.

``get_current_user_id`` used to hand-wire its own shared-secret HMAC decode.
``JWT_SECRET_KEY`` is a committed development value and ``DEV_ENVIRONMENTS``
exempts it from the production validator, so a forged HS256 token carrying any
``sub`` was accepted -- anyone with repository access could start transcoding
jobs as any identity, and could enumerate the jobs they owned. That is the live
bypass in #941. The guard now delegates to ``wildframe_auth``'s
``verify_token_with_jwks``.

The contract these tests pin is the status-code split, because that is what
clients and the gateway actually observe:

* the JWKS cannot be had (transport failure, or a body that is not a JWKS)
  -> **503** "Token verification is unavailable"
* the token is not valid (bad signature, expired, wrong audience, unknown kid)
  -> **401** "Invalid token"

Every token here is a real RS256 signature over the real RSA-2048 keypair
generated at import time in ``tests/_test_jwks.py``; only the outbound JWKS HTTP
call is replaced. The forgery test signs with the literal committed dev secret,
which is the point: that exact string used to be a valid key here.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import wildframe_auth
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt

import app.api.media_pipeline_routes as routes
from app.api.media_pipeline_routes import (
    _decode_token,
    get_current_user_id,
    get_pipeline_service,
    router,
)
from app.core.settings import settings
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

#: The key set auth-service publishes after a rotation (same material, new kid).
ROTATED_JWKS = {"keys": [*JWKS["keys"], {**JWKS["keys"][0], "kid": "k2"}]}

#: The secret that ``deployments/docker-compose.dev.yml`` commits. Before this
#: migration it was the *valid* verification key for this service.
COMMITTED_DEV_SECRET = "dev-secret-key"

#: A guarded path: the token boundary decides the response before the handler
#: (and therefore the state machine) is ever reached.
START_PATH = f"/api/v1/pipeline/jobs/{uuid.uuid4()}/start"

START_BODY = {
    "content_id": str(uuid.uuid4()),
    "upload_session_id": str(uuid.uuid4()),
    "storage_key": "uploads/x/v.mp4",
    "idempotency_key": "test-key",
}


def _mint(
    *,
    kid: str = "k1",
    typ: str = "access",
    exp_offset: int = 300,
    aud: str | None = None,
    iss: str | None = None,
    sub: str | None = None,
    private_pem: str = PRIVATE_PEM,
    algorithm: str = "RS256",
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": sub or str(uuid.uuid4()),
        "type": typ,
        "aud": aud or settings.JWT_AUDIENCE,
        "iss": iss or settings.JWT_ISSUER,
        "av": 0,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp() + exp_offset),
    }
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _Endpoint:
    """The auth service's JWKS endpoint, as the route sees it."""

    def __init__(self) -> None:
        self.jwks = JWKS
        self.calls: list[str] = []
        self.raises: Exception | None = None
        self.body = None

    async def fetch(self, url: str):
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        return self.body if self.body is not None else self.jwks

    @property
    def fetches(self) -> int:
        return len(self.calls)


@pytest.fixture
def endpoint(monkeypatch) -> _Endpoint:
    """Replace the outbound JWKS fetch only; the SDK verifier still runs for real."""
    ep = _Endpoint()
    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", ep.fetch)
    clear_jwks_cache()
    yield ep
    clear_jwks_cache()


@pytest.fixture
def pipeline_client():
    """A client on the real pipeline router.

    The service dependency is stubbed: a 401/503 is decided at the token
    boundary, so the handler and the state machine are never reached.
    """
    stub = MagicMock()
    stub.start_job = AsyncMock()
    stub.advance = AsyncMock()
    stub.job_repo = MagicMock(get=AsyncMock(return_value=None))
    stub.log_repo = MagicMock(list_for_job=AsyncMock(return_value=[]))

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_pipeline_service] = lambda: stub
    try:
        with TestClient(app, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Finding 1 -- the bypass itself (#941)
# ---------------------------------------------------------------------------


class TestForgedSharedSecretTokenIsRejected:
    """The regression these tests exist for.

    Against the old shared-secret decode every one of these was *accepted*: the
    forgery is signed with the very secret the service used to verify with.
    """

    async def test_the_committed_dev_secret_is_not_a_valid_key(self, endpoint):
        forged = _mint(private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await _decode_token(forged)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_forged_token_cannot_resolve_an_identity(self, endpoint):
        """The old guard returned the forged ``sub`` as a UUID. Now: nothing."""
        forged = _mint(private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(f"Bearer {forged}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    def test_a_forged_token_cannot_start_a_job(self, endpoint, pipeline_client):
        forged = _mint(private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        response = pipeline_client.post(START_PATH, json=START_BODY, headers=_bearer(forged))

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    def test_the_route_no_longer_reaches_for_the_shared_secret(self):
        """The bypass is removed structurally, not merely out-tested."""
        assert routes.verify_token_with_jwks is wildframe_auth.verify_token_with_jwks
        assert not hasattr(routes, "get_cached_jwks")
        assert not hasattr(routes, "verify_jwt_token")
        assert routes.settings.JWT_JWKS_URL


class TestGenuineRs256IsAccepted:
    async def test_a_genuine_access_token_verifies(self, endpoint):
        sub = str(uuid.uuid4())

        payload = await _decode_token(_mint(sub=sub))

        assert payload["sub"] == sub

    async def test_a_genuine_token_reaches_the_user_id(self, endpoint):
        sub = str(uuid.uuid4())

        assert str(await get_current_user_id(f"Bearer {_mint(sub=sub)}")) == sub

    def test_a_genuine_token_reaches_the_handler(self, endpoint, pipeline_client):
        """Authentication passing is distinguishable from the guard refusing it."""
        sub = str(uuid.uuid4())

        response = pipeline_client.get(
            f"/api/v1/pipeline/jobs/{uuid.uuid4()}",
            headers=_bearer(_mint(sub=sub)),
        )

        # The stub reports no such job, so a 404 proves the guard let the caller
        # through; a 401/503 would mean it did not.
        assert response.status_code == 404

    async def test_the_sdk_rotates_a_key_without_waiting_out_the_cache_ttl(self, endpoint):
        """A rotation lands immediately instead of 401ing for up to 300s.

        The old hand-wiring had no refresh path at all, so every token signed
        with a newly published key was rejected until the cache expired.
        """
        assert (await _decode_token(_mint()))["sub"]
        assert endpoint.fetches == 1

        endpoint.jwks = ROTATED_JWKS
        assert (await _decode_token(_mint(kid="k2")))["sub"]
        assert endpoint.fetches == 2, "the warm cache must be refreshed once"

    async def test_a_rotated_token_reaches_the_user_id(self, endpoint):
        assert await get_current_user_id(f"Bearer {_mint()}")
        endpoint.jwks = ROTATED_JWKS
        assert await get_current_user_id(f"Bearer {_mint(kid='k2')}")


# ---------------------------------------------------------------------------
# The 503 / 401 split
# ---------------------------------------------------------------------------


class TestJwksOutageIs503:
    async def test_a_transport_failure_is_503(self, endpoint):
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint())

        assert exc.value.status_code == 503
        assert exc.value.detail == "Token verification is unavailable"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(["not", "a", "jwks"], id="json-array"),
            pytest.param("<html>502 Bad Gateway</html>", id="html-string"),
            pytest.param({"keys": "k1"}, id="keys-not-a-list"),
            pytest.param({"keys": [{"kid": "k1"}]}, id="key-without-kty"),
        ],
    )
    async def test_a_malformed_jwks_body_is_503_not_500(self, endpoint, body):
        """A poisoned JWKS must not become a 500, nor stay cached."""
        endpoint.body = body

        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint())

        assert exc.value.status_code == 503
        assert exc.value.detail == "Token verification is unavailable"

    async def test_a_malformed_body_does_not_poison_the_cache(self, endpoint):
        endpoint.body = {"keys": "garbage"}
        for _ in range(3):
            with pytest.raises(HTTPException) as exc:
                await _decode_token(_mint())
            assert exc.value.status_code == 503
        assert endpoint.fetches == 3  # never cached, so always retried

    async def test_a_rotation_time_outage_is_503_not_a_500(self, endpoint):
        """A failed *forced* refetch must not escape as a bare transport error."""
        assert await _decode_token(_mint())
        endpoint.raises = ConnectionError("auth-service dropped the refresh")

        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(kid="k-rotated"))

        assert exc.value.status_code == 503
        assert exc.value.detail == "Token verification is unavailable"


class TestBadTokenIs401:
    async def test_a_garbage_token_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token("not-a-jwt")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_an_expired_token_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(exp_offset=-10_000))

        assert exc.value.status_code == 401

    async def test_a_token_signed_by_another_key_is_401(self, endpoint):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        intruder = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = intruder.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(private_pem=pem))

        assert exc.value.status_code == 401

    async def test_a_wrong_audience_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(aud="somebody-else"))

        assert exc.value.status_code == 401

    async def test_a_wrong_issuer_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(iss="https://evil.test"))

        assert exc.value.status_code == 401

    async def test_an_unknown_kid_is_401(self, endpoint):
        """The rotation refresh must not turn "unknown" into "accepted"."""
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(kid="k-never-published"))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    @pytest.mark.parametrize("typ", ["refresh", "logout", ""])
    async def test_a_non_access_token_type_is_401(self, endpoint, typ):
        """Token-type separation (#221) survives the move to the shared verifier."""
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(typ=typ))

        assert exc.value.status_code == 401

    def test_a_bad_token_is_a_jwt_error_not_an_outage(self):
        """The 401 branch is keyed on ``JWTError``, and the 503 type is not a
        parent of ``UnknownKidError`` -- the ordering the split depends on."""
        from wildframe_auth import JWKSUnavailableError, UnknownKidError

        assert issubclass(UnknownKidError, JWTError)
        assert not issubclass(UnknownKidError, JWKSUnavailableError)
        assert issubclass(JWKSUnavailableError, JWTError)


# ---------------------------------------------------------------------------
# The same split, as the client sees it
# ---------------------------------------------------------------------------


class TestStatusCodesOverHttp:
    def test_a_jwks_outage_reaches_the_client_as_503(self, endpoint, pipeline_client):
        endpoint.raises = ConnectionError("auth-service unreachable")

        response = pipeline_client.post(START_PATH, json=START_BODY, headers=_bearer(_mint()))

        assert response.status_code == 503
        assert response.json()["detail"] == "Token verification is unavailable"

    def test_a_bad_token_reaches_the_client_as_401(self, endpoint, pipeline_client):
        response = pipeline_client.post(
            START_PATH, json=START_BODY, headers={"Authorization": "Bearer not-a-jwt"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    def test_a_missing_header_is_still_401(self, endpoint, pipeline_client):
        response = pipeline_client.post(START_PATH, json=START_BODY)

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid authorization header"

    def test_a_malformed_jwks_body_reaches_the_client_as_503(self, endpoint, pipeline_client):
        endpoint.body = ["not", "a", "jwks"]

        response = pipeline_client.post(START_PATH, json=START_BODY, headers=_bearer(_mint()))

        assert response.status_code == 503

    def test_a_forged_token_never_starts_work(self, endpoint, pipeline_client):
        """The refusal must land before the state machine is touched."""
        forged = _mint(private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        response = pipeline_client.post(START_PATH, json=START_BODY, headers=_bearer(forged))

        assert response.status_code == 401
