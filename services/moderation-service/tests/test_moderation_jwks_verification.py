"""The moderation-service token boundary, after it was moved onto the SDK verifier.

``_verify_token`` used to hand-wire its own shared-secret HMAC decode.
``JWT_SECRET_KEY`` is a committed development value and ``DEV_ENVIRONMENTS``
exempts it from the production validator, so a forged HS256 token carrying
``role: "admin"``, any ``sub`` and a matching ``arv`` reached the moderation
queue, the decisions endpoint, and the DMCA workflows behind them. That is the
live bypass in #941. The guard now delegates to ``wildframe_auth``'s
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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wildframe_auth
from fastapi import HTTPException
from jose import JWTError, jwt

import app.api.moderation_routes as routes
from app.api.moderation_routes import (
    _decode_token,
    _verify_token,
    get_current_admin_id,
    get_current_user_id,
)
from app.core.settings import settings
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

#: The key set auth-service publishes after a rotation (same material, new kid).
ROTATED_JWKS = {"keys": [*JWKS["keys"], {**JWKS["keys"][0], "kid": "k2"}]}

#: The secret that ``deployments/docker-compose.dev.yml`` commits. Before this
#: migration it was the *valid* verification key for this service.
COMMITTED_DEV_SECRET = "dev-secret-key"


def _mint(
    *,
    kid: str = "k1",
    typ: str = "access",
    role: str = "user",
    av: int = 2,
    arv: int | None = None,
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
        "role": role,
        "aud": aud or settings.JWT_AUDIENCE,
        "iss": iss or settings.JWT_ISSUER,
        "av": av,
        "arv": settings.ADMIN_ROLE_VERSION if arv is None else arv,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp() + exp_offset),
    }
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


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


def _introspection(*, status_code: int = 200, payload: dict | None = None):
    """An auth-service ``/auth/me`` response double."""
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=payload if payload is not None else {})

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)
    return client


@pytest.fixture(autouse=True)
def _stub_introspection():
    """Stub only the auth-version round-trip.

    A 401/503 is decided at the token boundary, before introspection runs, so
    this stub cannot weaken the outage and rejection cases below. The token
    itself is still verified for real by the SDK.
    """
    with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=_introspection()):
        yield


# ---------------------------------------------------------------------------
# Finding 1 -- the bypass itself (#941)
# ---------------------------------------------------------------------------


class TestForgedSharedSecretTokenIsRejected:
    """The regression these tests exist for.

    Against the old shared-secret decode every one of these was *accepted*: the
    forgery is signed with the very secret the service used to verify with, and
    it asserts ``role: "admin"`` with a matching ``arv`` to get past both
    authorization checks.
    """

    async def test_the_committed_dev_secret_is_not_a_valid_key(self, endpoint):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await _decode_token(forged)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_forged_admin_token_cannot_reach_the_admin_dependency(self, endpoint):
        """The exact payload the old code would have accepted as an admin."""
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(f"Bearer {forged}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_forged_admin_token_cannot_reach_the_user_dependency(self, endpoint):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(f"Bearer {forged}")

        assert exc.value.status_code == 401

    async def test_a_forged_token_cannot_reach_the_admin_queue(self, endpoint):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {forged}", require_admin=True)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

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

    async def test_a_genuine_user_token_reaches_the_user_id(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(f"Bearer {_mint(sub=sub)}") == sub

    async def test_a_genuine_admin_token_reaches_the_admin_id(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_admin_id(f"Bearer {_mint(sub=sub, role='admin')}") == sub

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

    async def test_a_rotated_token_reaches_the_admin_id(self, endpoint):
        assert await get_current_admin_id(f"Bearer {_mint(role='admin')}")
        endpoint.jwks = ROTATED_JWKS
        assert await get_current_admin_id(f"Bearer {_mint(role='admin', kid='k2')}")


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

    @pytest.mark.parametrize("require_admin", [False, True], ids=["user", "admin"])
    async def test_both_guards_report_the_outage_as_503(self, endpoint, require_admin):
        """The user guard and the admin guard must not disagree about an outage."""
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {_mint(role='admin')}", require_admin=require_admin)

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

    @pytest.mark.parametrize("typ", ["refresh", "admin_step_up", "api_key", ""])
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
# Authorization is unchanged: who is allowed did not change, only how the
# signature is checked. The ``role`` and ``arv`` checks are moderation's own
# and are the reason this file pins them separately.
# ---------------------------------------------------------------------------


class TestAuthorizationChecksUnchanged:
    async def test_admin_is_still_admin(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_admin_id(f"Bearer {_mint(sub=sub, role='admin')}") == sub

    async def test_user_is_still_a_user(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(f"Bearer {_mint(sub=sub, role='user')}") == sub

    async def test_a_genuine_user_token_is_403_on_the_admin_dependency(self, endpoint):
        """A *valid* signature is not a pass: a non-admin is still forbidden."""
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id(f"Bearer {_mint(role='user')}")

        assert exc.value.status_code == 403
        assert exc.value.detail == "Admin privileges required"

    async def test_a_missing_role_is_still_403(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {_mint(role='user')}", require_admin=True)

        assert exc.value.status_code == 403

    async def test_a_stale_admin_role_version_is_still_403(self, endpoint):
        """``arv`` is moderation's own step-up-adjacent check, not the SDK's."""
        with pytest.raises(HTTPException) as exc:
            await _verify_token(
                f"Bearer {_mint(role='admin', arv=settings.ADMIN_ROLE_VERSION + 1)}",
                require_admin=True,
            )

        assert exc.value.status_code == 403
        assert exc.value.detail == "Admin privileges required"

    async def test_a_matching_admin_role_version_is_accepted(self, endpoint):
        sub = str(uuid.uuid4())

        resolved = await _verify_token(
            f"Bearer {_mint(sub=sub, role='admin', arv=settings.ADMIN_ROLE_VERSION)}",
            require_admin=True,
        )

        assert resolved == sub

    async def test_a_revoked_token_is_401_even_with_a_valid_signature(self, endpoint):
        """Introspection still runs, and it still precedes the role check."""
        with patch(
            "app.api.moderation_routes.httpx.AsyncClient",
            return_value=_introspection(status_code=401),
        ):
            with pytest.raises(HTTPException) as exc:
                await _verify_token(f"Bearer {_mint(role='admin')}", require_admin=True)

        assert exc.value.status_code == 401
