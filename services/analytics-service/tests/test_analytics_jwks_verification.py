"""The analytics-service token boundary, after it was moved onto the SDK verifier.

``get_current_user_claims`` used to hand-wire its own shared-secret HMAC decode.
``JWT_SECRET_KEY`` is a committed development value and ``DEV_ENVIRONMENTS``
exempts it from the production validator, so a forged HS256 token carrying any
``sub`` and ``role: "admin"`` was accepted -- the live bypass in #941. Signature
verification is now delegated to ``wildframe_auth``'s
:func:`verify_token_with_jwks` through ``_decode_token``.

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
from unittest.mock import AsyncMock

import pytest
import wildframe_auth
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt

import app.api.analytics_routes as routes
from app.api.analytics_routes import (
    _decode_token,
    get_current_user_claims,
    get_current_user_id,
    require_creator_access,
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

#: Guarded paths: the token boundary decides the response before the handler
#: (and therefore the database) is ever reached.
USER_PATH = f"/api/v1/analytics/user-events/{uuid.uuid4()}"
ADMIN_PATH = f"/api/v1/analytics/creators/{uuid.uuid4()}"


def _mint(
    *,
    kid: str = "k1",
    typ: str = "access",
    role: str = "user",
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
        "role": role,
        "av": 0,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp() + exp_offset),
    }
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _Endpoint:
    """The auth service's JWKS endpoint, as the routes see it."""

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
def analytics_client():
    """A client on the real analytics router.

    The service dependency is stubbed, so an authorised request reaches the
    handler and gets a deterministic empty result. A 401 or 503 therefore means
    the token boundary stopped it, and a 200 means the boundary let it through
    -- which is exactly the distinction these tests need, and the reason no
    real database is involved.
    """

    def _service() -> AsyncMock:
        service = AsyncMock()
        service.get_user_events = AsyncMock(return_value=[])
        service.get_creator_analytics = AsyncMock(return_value=None)
        return service

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[routes.get_analytics_service] = _service
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
    forgery is signed with the very secret the service used to verify with, and
    it asserts ``role: "admin"`` to reach privileged analytics.
    """

    async def test_the_committed_dev_secret_is_not_a_valid_key(self, endpoint):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await _decode_token(forged)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_forged_admin_token_cannot_reach_the_claims_dependency(self, endpoint):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_claims(f"Bearer {forged}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_forged_admin_token_cannot_reach_the_user_id_dependency(self, endpoint):
        """A forged *admin* token is not merely downgraded to a user -- it is refused.

        ``get_current_user_id`` reaches the boundary through ``get_current_user_claims``,
        so this drives the whole chain rather than the leaf.
        """
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(await get_current_user_claims(f"Bearer {forged}"))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    def test_a_forged_admin_token_is_401_over_http(self, endpoint, analytics_client):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        response = analytics_client.get(ADMIN_PATH, headers=_bearer(forged))

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    def test_the_route_no_longer_reaches_for_the_shared_secret(self):
        """The bypass is removed structurally, not merely out-tested.

        Asserting on the *absence* of the secret at the boundary means the check
        still holds if someone reintroduces an inline decode that a forged token
        happens not to exercise.
        """
        assert routes.verify_token_with_jwks is wildframe_auth.verify_token_with_jwks
        assert not hasattr(routes, "get_cached_jwks")
        assert not hasattr(routes, "verify_jwt_token")
        assert routes.settings.JWT_JWKS_URL


class TestGenuineRs256IsAccepted:
    async def test_a_genuine_access_token_verifies(self, endpoint):
        sub = str(uuid.uuid4())

        payload = await _decode_token(_mint(sub=sub))

        assert payload["sub"] == sub

    async def test_a_genuine_token_reaches_the_claims(self, endpoint):
        sub = str(uuid.uuid4())

        claims = await get_current_user_claims(f"Bearer {_mint(sub=sub, role='creator')}")

        assert str(claims["user_id"]) == sub
        assert claims["role"] == "creator"

    async def test_a_genuine_token_reaches_the_user_id(self, endpoint):
        sub = str(uuid.uuid4())

        assert (
            str(
                await get_current_user_id(await get_current_user_claims(f"Bearer {_mint(sub=sub)}"))
            )
            == sub
        )

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

    async def test_a_rotated_token_reaches_the_claims(self, endpoint):
        assert await get_current_user_claims(f"Bearer {_mint()}")
        endpoint.jwks = ROTATED_JWKS
        assert await get_current_user_claims(f"Bearer {_mint(kid='k2')}")


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

    async def test_the_claims_dependency_reports_the_outage_as_503(self, endpoint):
        """The boundary must not answer an outage as if the token were bad."""
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_claims(f"Bearer {_mint()}")

        assert exc.value.status_code == 503


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

    async def test_a_refresh_token_is_401(self, endpoint):
        """Token-type separation (#221) survives the move to the shared verifier.

        The detail is now the generic ``"Invalid token"`` rather than the old
        ``"Invalid token type"``: the type check now lives inside the shared
        verifier, so the service no longer learns *why* a token was rejected.
        Pinned so the message change is a decision, not a regression.
        """
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(typ="refresh"))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_user_id_only_token_is_refused_at_the_signature_boundary(self, endpoint):
        """``sub`` is now required by the shared verifier.

        The old decode accepted a payload carrying only ``user_id``, and this
        dependency still falls back to it -- but the fallback is now
        unreachable, because the verifier refuses a token with no ``sub`` before
        the fallback is ever consulted. Pinned so the dead branch is not
        mistaken for a working one.
        """
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "user_id": str(uuid.uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "av": 0,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        with pytest.raises(HTTPException) as exc:
            await get_current_user_claims(f"Bearer {token}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    def test_a_bad_token_is_a_jwt_error_not_an_outage(self):
        """The 401 branch is keyed on ``JWTError``, and the 503 type is not a
        parent of ``UnknownKidError`` -- the ordering the split depends on."""
        from wildframe_auth import JWKSUnavailableError, UnknownKidError

        assert issubclass(UnknownKidError, JWTError)
        assert not issubclass(UnknownKidError, JWKSUnavailableError)
        assert issubclass(JWKSUnavailableError, JWTError)


# ---------------------------------------------------------------------------
# Authorization is unchanged: who is allowed did not change, only how the
# signature is checked.
# ---------------------------------------------------------------------------


class TestRoleChecksStillApply:
    async def test_a_genuine_admin_token_stays_privileged(self, endpoint):
        """``PRIVILEGED_ROLE`` still short-circuits creator ownership."""
        from fastapi import Request

        creator = uuid.uuid4()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/analytics/creators/x",
                "headers": [],
                "path_params": {"creator_id": str(creator)},
                "query_string": b"",
            }
        )
        claims = await get_current_user_claims(f"Bearer {_mint(role=settings.PRIVILEGED_ROLE)}")

        assert await require_creator_access(claims, request) == creator

    async def test_a_genuine_user_token_is_still_not_privileged(self, endpoint):
        """A *valid* signature is not a pass: a plain user stays out of scope."""
        from fastapi import Request

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/analytics/creators/x",
                "headers": [],
                "path_params": {"creator_id": str(uuid.uuid4())},
                "query_string": b"",
            }
        )
        claims = await get_current_user_claims(f"Bearer {_mint(role='user')}")

        with pytest.raises(HTTPException) as exc:
            await require_creator_access(claims, request)

        assert exc.value.status_code == 404
        assert exc.value.detail == "Not found"

    def test_a_valid_admin_token_reaches_the_privileged_route(self, endpoint, analytics_client):
        """A genuine admin token gets past the boundary into the handler."""
        response = analytics_client.get(ADMIN_PATH, headers=_bearer(_mint(role="admin")))

        assert response.status_code == 200

    def test_a_valid_user_token_is_404_on_the_privileged_route(self, endpoint, analytics_client):
        response = analytics_client.get(ADMIN_PATH, headers=_bearer(_mint(role="user")))

        assert response.status_code == 404
        assert response.json()["detail"] == "Not found"


# ---------------------------------------------------------------------------
# The same split, as the client sees it
# ---------------------------------------------------------------------------


class TestStatusCodesOverHttp:
    def test_a_jwks_outage_reaches_the_client_as_503(self, endpoint, analytics_client):
        endpoint.raises = ConnectionError("auth-service unreachable")

        response = analytics_client.get(USER_PATH, headers=_bearer(_mint()))

        assert response.status_code == 503
        assert response.json()["detail"] == "Token verification is unavailable"

    def test_a_bad_token_reaches_the_client_as_401(self, endpoint, analytics_client):
        response = analytics_client.get(USER_PATH, headers={"Authorization": "Bearer not-a-jwt"})

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    def test_a_missing_header_is_still_401(self, endpoint, analytics_client):
        response = analytics_client.get(USER_PATH)

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid authorization header"

    def test_a_malformed_jwks_body_reaches_the_client_as_503(self, endpoint, analytics_client):
        endpoint.body = ["not", "a", "jwks"]

        response = analytics_client.get(USER_PATH, headers=_bearer(_mint()))

        assert response.status_code == 503
