"""The uploads-service token boundary, after it was moved onto the SDK verifier.

``get_current_user_id`` used to hand-wire a shared-secret HMAC decode.
``JWT_SECRET_KEY`` is a committed development value and ``DEV_ENVIRONMENTS``
exempts it from the production validator, so a forged HS256 token carrying any
``sub`` was accepted -- the live bypass in #941. On an upload service that is
the write path: a forged token reaches session creation, chunk registration,
completion and abort. The dependency now delegates to ``wildframe_auth``'s
``verify_token_with_jwks`` through ``_decode_token``.

The contract these tests pin is the status-code split, because that is what
clients and the gateway actually observe:

* the JWKS cannot be had (transport failure, or a body that is not a JWKS)
  -> **503** "Token verification is unavailable"
* the token is not valid (bad signature, expired, wrong audience, unknown kid)
  -> **401** "Invalid token"

Every token here is a real RS256 signature over the real RSA-2048 keypair
generated at import time in ``tests/_test_jwks.py``; only the outbound JWKS HTTP
call is replaced. The forgery tests sign with the literal committed dev secrets,
which is the point: those exact strings used to be valid keys here.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import wildframe_auth
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt

import app.api.uploads_routes as routes
from app.api.uploads_routes import _decode_token, get_current_user_id, get_upload_service
from app.core.settings import settings
from app.main import app
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

#: Guarded path: the token boundary decides the response before the handler
#: (and therefore the database) is ever reached.
SESSIONS_PATH = "/api/v1/uploads/sessions"


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
def uploads_client():
    """Client on the real router and the real auth chain.

    Only ``get_upload_service`` is stubbed, so no database is involved. A 401 or
    503 therefore means the token boundary stopped the request, and a 400 means
    the boundary let it through into the handler (where a stubbed
    ``create_session`` raises ``UploadError``) -- which is exactly the
    distinction these tests need.
    """

    def _service() -> MagicMock:
        from app.services import UploadError

        service = MagicMock()
        service.create_session = AsyncMock(side_effect=UploadError("stubbed"))
        return service

    app.dependency_overrides.clear()
    app.dependency_overrides[get_upload_service] = _service
    try:
        # Deliberately not a context manager: entering one runs the app lifespan,
        # which raises without a healthy database.
        yield TestClient(app, base_url="http://test")
    finally:
        app.dependency_overrides.clear()


_CREATE_BODY = {
    "creator_id": str(uuid.uuid4()),
    "filename": "clip.mp4",
    "mime": "video/mp4",
    "size_bytes": 1024,
}


# ---------------------------------------------------------------------------
# Finding 1 -- the bypass itself (#941)
# ---------------------------------------------------------------------------


class TestForgedSharedSecretTokenIsRejected:
    """The regression these tests exist for.

    Against the old shared-secret decode every one of these was *accepted*: the
    forgery is signed with the very secret the service used to verify with, and
    it is the write path, so a forged token could create and finalize sessions.
    """

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_the_committed_dev_secret_is_not_a_valid_key(self, endpoint, secret):
        forged = _mint(private_pem=secret, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await _decode_token(forged)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    async def test_a_forged_token_cannot_resolve_an_identity(self, endpoint, secret):
        forged = _mint(private_pem=secret, algorithm="HS256")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(f"Bearer {forged}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    @pytest.mark.parametrize("secret", COMMITTED_DEV_SECRETS)
    def test_a_forged_token_is_401_over_http(self, endpoint, uploads_client, secret):
        forged = _mint(private_pem=secret, algorithm="HS256")

        response = uploads_client.post(SESSIONS_PATH, json=_CREATE_BODY, headers=_bearer(forged))

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

    async def test_a_genuine_token_reaches_the_user_id(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(f"Bearer {_mint(sub=sub)}") == uuid.UUID(sub)

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

    async def test_the_dependency_reports_the_outage_as_503(self, endpoint):
        endpoint.raises = ConnectionError("auth-service unreachable")

        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(f"Bearer {_mint()}")

        assert exc.value.status_code == 503


class TestBadTokenIs401:
    async def test_a_garbage_token_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token("not-a-jwt")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_an_expired_token_is_401(self, endpoint):
        # -1h, not -60s: the shared verifier allows ~60s of clock skew, so a
        # token that expired "just now" is *meant* to pass.
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(exp_offset=-3600))

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

        This is the guard on the upload write surface, so a refresh token must
        not be able to create or finalize a session.
        """
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(typ="refresh"))

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_token_without_a_sub_is_refused_at_the_signature_boundary(self, endpoint):
        """``sub`` is a required claim, so the ``user_id`` fallback cannot be reached."""
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
            await _decode_token(token)

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_non_uuid_subject_is_still_an_invalid_subject(self, endpoint):
        """A present-but-malformed ``sub`` is still a *subject* error, not a token error.

        The message is deliberately unchanged: only the signature check moved.
        """
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(f"Bearer {_mint(sub='not-a-uuid')}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token subject"

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


class TestRoleAndOwnershipChecksStillApply:
    """Uploads is self-service: the check is creator-id equality, not a role.

    The role claim is irrelevant to the outcome, asserted in both directions so
    a future change cannot quietly start treating an admin token as *more*
    entitled, or a user token as less.
    """

    async def test_admin_is_still_admin(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(f"Bearer {_mint(sub=sub, role='admin')}") == uuid.UUID(sub)

    async def test_user_is_still_a_user(self, endpoint):
        sub = str(uuid.uuid4())

        assert await get_current_user_id(f"Bearer {_mint(sub=sub, role='user')}") == uuid.UUID(sub)

    def test_a_valid_token_reaches_the_handler(self, endpoint, uploads_client):
        """A genuine token matching ``creator_id`` gets into the handler (400)."""
        creator = uuid.uuid4()
        body = {**_CREATE_BODY, "creator_id": str(creator)}

        response = uploads_client.post(
            SESSIONS_PATH, json=body, headers=_bearer(_mint(sub=str(creator)))
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "stubbed"

    def test_a_valid_token_for_another_creator_is_still_403(self, endpoint, uploads_client):
        """Ownership still holds: a valid signature is not a pass."""
        body = {**_CREATE_BODY, "creator_id": str(uuid.uuid4())}

        response = uploads_client.post(
            SESSIONS_PATH, json=body, headers=_bearer(_mint(sub=str(uuid.uuid4())))
        )

        assert response.status_code == 403
        assert response.json()["detail"] == (
            "You can only create upload sessions for your own account"
        )

    def test_an_admin_token_cannot_act_as_another_creator(self, endpoint, uploads_client):
        """Admin does not bypass the ownership check on the write path."""
        body = {**_CREATE_BODY, "creator_id": str(uuid.uuid4())}

        response = uploads_client.post(
            SESSIONS_PATH, json=body, headers=_bearer(_mint(role="admin"))
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# The same split, as the client sees it
# ---------------------------------------------------------------------------


class TestStatusCodesOverHttp:
    def test_a_jwks_outage_reaches_the_client_as_503(self, endpoint, uploads_client):
        endpoint.raises = ConnectionError("auth-service unreachable")

        response = uploads_client.post(SESSIONS_PATH, json=_CREATE_BODY, headers=_bearer(_mint()))

        assert response.status_code == 503
        assert response.json()["detail"] == "Token verification is unavailable"

    def test_a_bad_token_reaches_the_client_as_401(self, endpoint, uploads_client):
        response = uploads_client.post(
            SESSIONS_PATH, json=_CREATE_BODY, headers={"Authorization": "Bearer not-a-jwt"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    def test_a_missing_header_is_still_401(self, endpoint, uploads_client):
        response = uploads_client.post(SESSIONS_PATH, json=_CREATE_BODY)

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid authorization header"

    def test_a_malformed_jwks_body_reaches_the_client_as_503(self, endpoint, uploads_client):
        endpoint.body = ["not", "a", "jwks"]

        response = uploads_client.post(SESSIONS_PATH, json=_CREATE_BODY, headers=_bearer(_mint()))

        assert response.status_code == 503
