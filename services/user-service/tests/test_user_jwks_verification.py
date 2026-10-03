"""The user-service token boundary, after it was moved onto the SDK verifier.

``app/security/manager.py`` is a *library* module, so this migration had two
hazards the route-module migrations did not:

1. ``TokenManager.verify_token`` was **synchronous**, and the replacement
   ``verify_token_with_jwks`` fetches the JWKS over the network. It is now a
   coroutine. This file exists partly to pin that its one caller awaits it -- a
   missed ``await`` would not raise, it would return a coroutine, and
   ``if not payload`` is true for a coroutine object, so the route would answer
   401 for a perfectly good token.

2. The old signature returned ``None`` for *every* failure, including a missing
   signing key. With a JWKS in the path, an outage must be distinguishable from
   a bad token or the service would tell callers their token was bad when it
   merely could not check it. So ``JWKSUnavailableError`` propagates and
   ``get_current_user_id`` turns it into a 503.

The module also *minted* tokens. ``TokenManager.create_access_token`` signed
with the committed development secret, which made it a forge-token factory
sitting in the same file as the verifier; it is deleted rather than migrated,
because after the verifier moved to the JWKS nothing would accept its output.

Every token here is a real RS256 signature over the real RSA-2048 keypair
generated at import time in ``tests/_test_jwks.py``; only the outbound JWKS HTTP
call is replaced.
"""

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import wildframe_auth
from fastapi import HTTPException
from jose import JWTError, jwt

import app.api.routes as routes
import app.security.manager as manager
from app.api.routes import get_current_user_id
from app.core.settings import settings
from app.security.manager import TokenManager
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

#: The key set auth-service publishes after a rotation (same material, new kid).
ROTATED_JWKS = {"keys": [*JWKS["keys"], {**JWKS["keys"][0], "kid": "k2"}]}

#: The two committed development secrets. Both are exempt from the production
#: validator, so either one was a *valid* verification key here:
#: ``dev-secret-key`` is what ``deployments/docker-compose.dev.yml`` injects into
#: the local stack, and ``dev-secret-key-change-in-production-min-32-bytes`` is the
#: pydantic default in ``app/core/settings.py`` that a service sees when the
#: variable is unset. Both are covered so the regression is pinned regardless of
#: which one the service under test actually loaded.
COMMITTED_DEV_SECRETS = ["dev-secret-key", "dev-secret-key-change-in-production-min-32-bytes"]


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
        "exp": int((now + timedelta(minutes=15)).timestamp()) + exp_offset,
    }
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


def _header(token: str) -> str:
    """The raw Authorization header value, as the dependency receives it."""
    return f"Bearer {token}"


class _Endpoint:
    """The auth service's JWKS endpoint, as the service sees it."""

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


# ---------------------------------------------------------------------------
# The async conversion itself -- the failure mode that silently loses auth
# ---------------------------------------------------------------------------


class TestVerifyTokenIsAwaited:
    """``TokenManager.verify_token`` became a coroutine; a missed await is silent.

    If a caller forgets to await, it holds a coroutine instead of a payload.
    ``if not payload`` is true for a coroutine object, so the route would answer
    401 with "Invalid or expired token" and nothing would be logged as an error.
    These are structural assertions because the symptom is otherwise invisible.
    """

    def test_verify_token_is_a_coroutine_function(self):
        assert inspect.iscoroutinefunction(TokenManager.verify_token)

    def test_the_single_caller_is_a_coroutine_function(self):
        assert inspect.iscoroutinefunction(get_current_user_id)

    async def test_awaiting_returns_a_payload_not_a_coroutine(self, endpoint):
        payload = await TokenManager.verify_token(_mint())

        assert isinstance(payload, dict)
        assert not inspect.isawaitable(payload)

    async def test_a_forgotten_await_would_be_truthy_and_break_the_route(self, endpoint):
        """Document *why* the assertions above matter, with the real failure mode.

        A coroutine object is truthy, so the route's ``if not payload`` guard
        does **not** catch a forgotten await. It sails past that check and
        then raises ``TypeError: argument of type 'coroutine' is not iterable``
        on ``"sub" not in payload`` -- surfacing as a 500 on every
        authenticated request, with no hint that the missing ``await`` is the
        cause. The structural assertions above are what actually prevent it.
        """
        un_awaited = TokenManager.verify_token(_mint())

        assert inspect.isawaitable(un_awaited)
        assert not isinstance(un_awaited, dict)
        # Truthy, so `if not payload` does not guard against it ...
        assert un_awaited
        # ... and the next clause is what actually blows up.
        with pytest.raises(TypeError):
            "sub" not in un_awaited
        # The contrast: awaited, it is a plain dict the guard handles correctly.
        awaited = await TokenManager.verify_token(_mint())
        assert isinstance(awaited, dict) and "sub" in awaited


# ---------------------------------------------------------------------------
# Finding 1 -- the bypass itself (#941)
# ---------------------------------------------------------------------------


class TestForgedSharedSecretTokenIsRejected:
    """The regression these tests exist for.

    Against the old shared-secret decode every one of these was *accepted*: the
    forgery is signed with the very secret the service used to verify with, and
    it asserts ``role: "admin"``.
    """

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_the_committed_dev_secret_is_not_a_valid_key(self, endpoint, secret):
        forged = _mint(role="admin", private_pem=secret, algorithm="HS256")

        assert await TokenManager.verify_token(forged) is None

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_a_forged_admin_token_cannot_reach_the_route(self, endpoint, secret):
        forged = _mint(role="admin", private_pem=secret, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(_header(forged))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    def test_the_module_no_longer_reaches_for_the_shared_secret(self):
        """The bypass is removed structurally, not merely out-tested."""
        assert manager.verify_token_with_jwks is wildframe_auth.verify_token_with_jwks
        assert manager.settings.JWT_JWKS_URL
        # The cursor-style check: no inline decode remains in this module at all.
        assert "jwt.decode(" not in inspect.getsource(manager)
        # And the route that consumes it must import the same verifier, so the
        # 503 mapping below is keyed on the right exception type.
        assert routes.JWKSUnavailableError is wildframe_auth.JWKSUnavailableError


class TestGenuineRs256IsAccepted:
    async def test_a_genuine_access_token_verifies(self, endpoint):
        sub = str(uuid.uuid4())

        payload = await TokenManager.verify_token(_mint(sub=sub))

        assert payload["sub"] == sub

    async def test_a_genuine_token_reaches_the_user_id(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(_header(_mint(sub=sub))) == uuid.UUID(sub)

    async def test_the_sdk_rotates_a_key_without_waiting_out_the_cache_ttl(self, endpoint):
        """A rotation lands immediately instead of 401ing for up to 300s.

        The old hand-wiring had no refresh path at all, so every token signed
        with a newly published key was rejected until the cache expired.
        """
        assert await TokenManager.verify_token(_mint())
        assert endpoint.fetches == 1

        endpoint.jwks = ROTATED_JWKS
        assert await TokenManager.verify_token(_mint(kid="k2"))
        assert endpoint.fetches == 2, "the warm cache must be refreshed once"

    async def test_a_rotated_token_reaches_the_user_id(self, endpoint):
        assert await get_current_user_id(_header(_mint()))
        endpoint.jwks = ROTATED_JWKS
        assert await get_current_user_id(_header(_mint(kid="k2")))


# ---------------------------------------------------------------------------
# The 503 / 401 split
# ---------------------------------------------------------------------------


class TestJwksOutageIs503:
    async def test_an_outage_propagates_rather_than_becoming_none(self, endpoint):
        """``verify_token`` must NOT launder an outage into "invalid token".

        Returning ``None`` here is the tempting shortcut, because every other
        failure returns ``None`` -- and it is exactly wrong: the caller would
        answer 401 for a token it never actually checked.
        """
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(wildframe_auth.JWKSUnavailableError):
            await TokenManager.verify_token(_mint())

    async def test_an_outage_reaches_the_route_as_503(self, endpoint):
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(_header(_mint()))

        assert exc.value.status_code == 503
        assert exc.value.detail == "Token verification is unavailable"
        assert exc.value.headers["Retry-After"] == "5"

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
            await get_current_user_id(_header(_mint()))

        assert exc.value.status_code == 503
        assert exc.value.detail == "Token verification is unavailable"

    async def test_a_malformed_body_does_not_poison_the_cache(self, endpoint):
        endpoint.body = {"keys": "garbage"}
        for _ in range(3):
            with pytest.raises(HTTPException) as exc:
                await get_current_user_id(_header(_mint()))
            assert exc.value.status_code == 503
        assert endpoint.fetches == 3  # never cached, so always retried

    async def test_a_rotation_time_outage_is_503_not_a_500(self, endpoint):
        """A failed *forced* refetch must not escape as a bare transport error."""
        assert await TokenManager.verify_token(_mint())
        endpoint.raises = ConnectionError("auth-service dropped the refresh")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(_header(_mint(kid="k-rotated")))

        assert exc.value.status_code == 503

    async def test_a_missing_header_is_still_401_not_503(self, endpoint):
        """No token means no JWKS fetch, so a missing header is a plain 401."""
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(None)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Missing or invalid authorization header"
        assert endpoint.fetches == 0


class TestBadTokenIsStillNone:
    """The pre-existing contract: a bad token is ``None``, never an exception."""

    async def test_a_garbage_token_is_none(self, endpoint):
        assert await TokenManager.verify_token("not-a-jwt") is None

    async def test_an_expired_token_is_none(self, endpoint):
        # -1h, not -60s: the shared verifier allows ~60s of clock skew, so a
        # token that expired "just now" is *meant* to pass.
        assert await TokenManager.verify_token(_mint(exp_offset=-3600)) is None

    async def test_a_token_signed_by_another_key_is_none(self, endpoint):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        intruder = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = intruder.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

        assert await TokenManager.verify_token(_mint(private_pem=pem)) is None

    async def test_a_wrong_audience_is_none(self, endpoint):
        assert await TokenManager.verify_token(_mint(aud="some-other-api")) is None

    async def test_a_wrong_issuer_is_none(self, endpoint):
        assert await TokenManager.verify_token(_mint(iss="https://evil.test")) is None

    async def test_an_unknown_kid_is_none(self, endpoint):
        """The rotation refresh must not turn "unknown" into "accepted"."""
        assert await TokenManager.verify_token(_mint(kid="k-never-published")) is None

    async def test_a_refresh_token_is_none_when_access_is_required(self, endpoint):
        """Token-type separation (#221) survives the move to the shared verifier."""
        token = _mint(typ="refresh")

        assert await TokenManager.verify_token(token) is None
        assert await TokenManager.verify_token(token, token_type="refresh") is not None

    async def test_a_refresh_token_is_401_at_the_route(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(_header(_mint(typ="refresh")))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    def test_a_bad_token_is_a_jwt_error_not_an_outage(self):
        """The 503 branch is keyed on ``JWKSUnavailableError``, not on ``JWTError``."""
        from wildframe_auth import JWKSUnavailableError, UnknownKidError

        assert issubclass(UnknownKidError, JWTError)
        assert not issubclass(UnknownKidError, JWKSUnavailableError)
        assert issubclass(JWKSUnavailableError, JWTError)


# ---------------------------------------------------------------------------
# Authorization is unchanged: who is allowed did not change, only how the
# signature is checked.
# ---------------------------------------------------------------------------


class TestRoleAndIdentityChecksStillApply:
    async def test_admin_is_still_admin(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(_header(_mint(sub=sub, role="admin"))) == uuid.UUID(sub)

    async def test_user_is_still_a_user(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(_header(_mint(sub=sub, role="user"))) == uuid.UUID(sub)

    async def test_a_non_uuid_sub_is_still_a_subject_error(self, endpoint):
        """A present-but-unusable ``sub`` is still a *subject* error, not a token error.

        The message is deliberately unchanged: only the signature check moved.
        """
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(_header(_mint(sub="not-a-uuid")))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token subject"
        assert exc.value.headers["WWW-Authenticate"] == "Bearer"

    async def test_the_verified_sub_is_the_whole_identity(self, endpoint):
        """A role claim cannot substitute for a subject.

        Under a shared-secret verifier an attacker chose both. Now the subject
        must be one the issuer signed, so an admin role with a foreign subject
        is just that subject -- it grants nothing extra here, and the route
        carries no role gate to abuse.
        """
        sub = str(uuid.uuid4())

        resolved = await get_current_user_id(_header(_mint(sub=sub, role="admin")))

        assert str(resolved) == sub
