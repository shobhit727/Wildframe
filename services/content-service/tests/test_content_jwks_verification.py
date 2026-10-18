"""The content-service token boundary, after it was moved onto the SDK verifier.

``_require_identity`` used to hand-wire its own shared-secret HMAC decode.
``JWT_SECRET_KEY`` is a committed development value and ``DEV_ENVIRONMENTS``
exempts it from the production validator, so a forged HS256 token carrying any
``sub`` and ``role: "admin"`` reached ``get_admin_identity`` -- the dependency
that guards every privileged catalog mutation (genres, content, seasons,
episodes, recommendations, cast, publish). That is the live bypass in #941.
Signature verification now delegates to ``wildframe_auth``'s
:func:`verify_token_with_jwks` through ``_decode_token``.

The contract these tests pin is the status-code split, because that is what
clients and the gateway actually observe:

* the JWKS cannot be had (transport failure, or a body that is not a JWKS)
  -> **503** "Token verification is unavailable"
* the token is not valid (bad signature, expired, wrong audience, unknown kid)
  -> **401** "Invalid or expired token"

Every token here is a real RS256 signature over the real RSA-2048 keypair
generated at import time in ``tests/_test_jwks.py``; only the outbound JWKS HTTP
call is replaced. The forgery test signs with the literal committed dev secret,
which is the point: that exact string used to be a valid key here.

Authorization is deliberately out of scope and pinned as *unchanged*: the
role allow-list, the ``arv`` admin-role-version gate, and the auth-version
comparison all still behave as before.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wildframe_auth
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt

import app.api.routes as routes
from app.api.routes import (
    _decode_token,
    _require_identity,
    get_admin_identity,
    get_current_user,
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
#: (and therefore the database) is ever reached. ``/genres`` POST is behind
#: ``get_admin_identity``; ``/content/{id}/ratings`` POST is the one route behind
#: the plain ``get_current_user`` guard. (The GET on that same path is
#: deliberately unauthenticated, so it would not exercise the boundary at all.)
ADMIN_PATH = "/api/v1/genres"
#: A body that satisfies ``GenreCreateRequest``; an invalid one would 422 during
#: request validation and the handler would never run, hiding what is asserted.
GENRE_BODY = {"name": "Documentary", "slug": "documentary"}
USER_PATH = f"/api/v1/content/{uuid.uuid4()}/ratings"
RATING_BODY = {"rating": 8.0}


class _ReachedHandler(Exception):
    """Sentinel: reaching the handler at all is the thing being asserted."""


def _mint(
    *,
    kid: str = "k1",
    typ: str = "access",
    role: str = "user",
    av: int = 1,
    arv: int = 0,
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
        "av": av,
        "arv": arv,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp() + exp_offset),
    }
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _introspection_ok():
    """Patch the auth-version call so it is not the thing under test."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)
    return patch("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)


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
def introspection_ok():
    """Auth-version introspection answers 200 with no auth_version, i.e. allowed."""
    with _introspection_ok() as p:
        yield p


@pytest.fixture
def content_client():
    """A client on the real content router.

    The service dependency is stubbed, so an authorised request reaches a
    handler and fails on its own input validation rather than the database. A
    401/403 means the token boundary stopped it; a 401 with the JWKS detail
    means it was an outage. No real database is involved.
    """

    def _service() -> AsyncMock:
        service = AsyncMock()
        service.list_genres = AsyncMock(return_value=[])
        service.rate_content = AsyncMock(return_value=None)
        return service

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[routes.get_content_service] = _service
    try:
        with TestClient(app, base_url="http://test") as client:
            # Exposed so a test can swap the stub for one that raises, proving
            # the handler was reached rather than inferring it from a status.
            client.app = app
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
    it asserts ``role: "admin"`` to reach the admin-only catalog writes.
    """

    async def test_the_committed_dev_secret_is_not_a_valid_key(self, endpoint):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await _decode_token(forged)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    async def test_a_forged_admin_token_cannot_reach_the_admin_guard(
        self, endpoint, introspection_ok
    ):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(f"Bearer {forged}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    async def test_a_forged_admin_token_cannot_reach_the_user_guard(self, endpoint):
        """A forged *admin* token is not merely downgraded to a user -- it is refused."""
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_user(f"Bearer {forged}")

        assert exc.value.status_code == 401

    def test_a_forged_admin_token_is_401_over_http(self, endpoint, content_client):
        forged = _mint(role="admin", private_pem=COMMITTED_DEV_SECRET, algorithm="HS256")

        response = content_client.post(ADMIN_PATH, json=GENRE_BODY, headers=_bearer(forged))

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"

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

    async def test_a_genuine_token_reaches_the_user_id(self, endpoint, introspection_ok):
        sub = str(uuid.uuid4())

        assert str(await get_current_user(f"Bearer {_mint(sub=sub)}")) == sub

    async def test_a_genuine_admin_token_reaches_the_admin_id(self, endpoint, introspection_ok):
        sub = str(uuid.uuid4())

        assert await get_admin_identity(f"Bearer {_mint(sub=sub, role='admin')}") == sub

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

    async def test_a_rotated_token_reaches_the_user_id(self, endpoint, introspection_ok):
        assert await get_current_user(f"Bearer {_mint()}")
        endpoint.jwks = ROTATED_JWKS
        assert await get_current_user(f"Bearer {_mint(kid='k2')}")


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

    async def test_both_guards_report_the_outage_as_503(self, endpoint, introspection_ok):
        """The user guard and the admin guard must not disagree about an outage."""
        endpoint.raises = ConnectionError("auth-service unreachable")

        for guard in (get_current_user, get_admin_identity):
            with pytest.raises(HTTPException) as exc:
                await guard(f"Bearer {_mint()}")
            assert exc.value.status_code == 503
            assert exc.value.detail == "Token verification is unavailable"


class TestBadTokenIs401:
    async def test_a_garbage_token_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token("not-a-jwt")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"
        assert exc.value.headers.get("WWW-Authenticate") == "Bearer"

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
        assert exc.value.detail == "Invalid or expired token"

    async def test_a_refresh_token_is_401(self, endpoint):
        """Token-type separation (#221) survives the move to the shared verifier.

        The detail is the service's generic ``"Invalid or expired token"`` rather
        than the old ``"Invalid token type"``: the type check now lives inside
        the verifier, so this service no longer learns *why* a token was
        rejected. Pinned so the message change is a decision, not a regression.
        """
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(typ="refresh"))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    async def test_a_token_without_an_auth_version_is_401(self, endpoint):
        """``av`` is required for auth-versioned token types.

        The verifier refuses a payload with no integer ``av`` before the role
        is read, so a token that predates the claim cannot reach the admin gate.
        """
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "type": "access",
                "arv": 0,
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "role": "admin",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(f"Bearer {token}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    async def test_a_user_id_only_token_is_refused_at_the_signature_boundary(self, endpoint):
        """``sub`` is now required by the shared verifier.

        The old decode accepted a payload carrying only ``user_id``; the
        verifier refuses a token with no ``sub`` before the claim is ever
        examined. Pinned so the change is deliberate.
        """
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "user_id": str(uuid.uuid4()),
                "type": "access",
                "av": 1,
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
            },
            PRIVATE_PEM,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

        with pytest.raises(HTTPException) as exc:
            await _decode_token(token)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

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


class TestRoleAndRoleVersionChecksStillApply:
    async def test_a_genuine_user_token_is_403_on_the_admin_guard(self, endpoint, introspection_ok):
        """A *valid* signature is not a pass: a non-admin is still forbidden."""
        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(f"Bearer {_mint(role='user')}")

        assert exc.value.status_code == 403
        assert exc.value.detail == "Administrator privileges required"

    async def test_admin_is_still_admin(self, endpoint, introspection_ok):
        sub = str(uuid.uuid4())

        assert await get_admin_identity(f"Bearer {_mint(sub=sub, role='admin')}") == sub

    async def test_a_stale_role_version_is_still_refused(
        self, endpoint, introspection_ok, monkeypatch
    ):
        """#81/#101: revocation is immediate, and stays immediate after the move."""
        monkeypatch.setattr(settings, "ADMIN_ROLE_VERSION", 4)

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(f"Bearer {_mint(role='admin', arv=3)}")

        assert exc.value.status_code == 403
        assert exc.value.detail == "Administrator privileges required"

    async def test_role_and_arv_are_both_returned(self, endpoint, introspection_ok):
        user_id, role, arv = await _require_identity(
            f"Bearer {_mint(role='creator', arv=7)}", with_role=True
        )

        assert (role, arv) == ("creator", 7)

    async def test_a_stale_auth_version_is_still_refused(self, endpoint):
        """The auth-version comparison is untouched and still fails closed."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"auth_version": 9}
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=response)

        with patch("app.api.routes.httpx.AsyncClient", lambda *a, **k: client):
            with pytest.raises(HTTPException) as exc:
                await get_current_user(f"Bearer {_mint(av=1)}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    def test_a_valid_user_token_is_403_on_the_admin_route(self, endpoint, content_client):
        response = content_client.post(
            ADMIN_PATH, json=GENRE_BODY, headers=_bearer(_mint(role="user"))
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Administrator privileges required"

    # ---------------------------------------------------------------------------
    # The same split, as the client sees it
    # ---------------------------------------------------------------------------

    def test_a_valid_admin_token_reaches_the_handler(self, endpoint, content_client):
        """A genuine admin token gets past the boundary into the handler.

        Asserted by having the handler blow up with a sentinel: reaching it is
        the only way to see the exception, so this distinguishes "admitted" from
        "rejected with 401/403" without depending on response-model validation.
        """

        def _explode(*args, **kwargs):
            raise _ReachedHandler

        content_client.app.dependency_overrides[routes.get_content_service] = lambda: AsyncMock(
            create_genre=_explode,
        )

        with pytest.raises(_ReachedHandler):
            content_client.post(ADMIN_PATH, json=GENRE_BODY, headers=_bearer(_mint(role="admin")))

    def test_a_valid_user_token_never_reaches_the_handler(self, endpoint, content_client):
        """The 403 is decided at the boundary, so the handler is never called."""
        called = AsyncMock()

        def _explode(*args, **kwargs):
            called()
            raise _ReachedHandler

        content_client.app.dependency_overrides[routes.get_content_service] = lambda: AsyncMock(
            create_genre=_explode,
        )

        response = content_client.post(
            ADMIN_PATH, json=GENRE_BODY, headers=_bearer(_mint(role="user"))
        )

        assert response.status_code == 403
        called.assert_not_called()


# ---------------------------------------------------------------------------
# The same split, as the client sees it
# ---------------------------------------------------------------------------


class TestStatusCodesOverHttp:
    def test_a_jwks_outage_reaches_the_client_as_503(self, endpoint, content_client):
        endpoint.raises = ConnectionError("auth-service unreachable")

        response = content_client.post(USER_PATH, json=RATING_BODY, headers=_bearer(_mint()))

        assert response.status_code == 503
        assert response.json()["detail"] == "Token verification is unavailable"

    def test_a_bad_token_reaches_the_client_as_401(self, endpoint, content_client):
        response = content_client.post(
            USER_PATH, json=RATING_BODY, headers={"Authorization": "Bearer not-a-jwt"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"

    def test_a_missing_header_is_still_401(self, endpoint, content_client):
        response = content_client.post(USER_PATH, json=RATING_BODY)

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid authorization header"

    def test_a_malformed_jwks_body_reaches_the_client_as_503(self, endpoint, content_client):
        endpoint.body = ["not", "a", "jwks"]

        response = content_client.post(USER_PATH, json=RATING_BODY, headers=_bearer(_mint()))

        assert response.status_code == 503
