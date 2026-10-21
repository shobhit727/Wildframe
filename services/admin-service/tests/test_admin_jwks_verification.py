"""The admin-service token boundary, after it was moved onto the SDK verifier.

``_decode_token`` used to hand-wire the two steps itself -- fetch the JWKS,
then verify -- which left it without any key-rotation handling and without a
validated JWKS document. It now goes through ``wildframe_auth``'s
``verify_token_with_jwks``.

The contract these tests pin is the status-code split, because that is what
clients and the gateway actually observe:

* the JWKS cannot be had (transport failure, or a body that is not a JWKS)
  -> **503** "Token verification is unavailable"
* the token is not valid (bad signature, expired, wrong audience, wrong type,
  unknown kid) -> **401** "Invalid token"

Every token here is a real RS256 signature over the real RSA-2048 keypair in
``tests/_test_jwks.py``; only the outbound JWKS HTTP call is replaced.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import wildframe_auth
from app.api.routes import admin as admin_routes
from app.api.routes.admin import _decode_token, get_current_admin_id, router
from app.core.database import DatabaseManager, get_db
from app.core.settings import settings
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

ADMIN = "admin-1"

#: The rotated key the test publishes mid-flight (same fixture key, new kid).
ROTATED_JWKS = {
    "keys": [*JWKS["keys"], {**JWKS["keys"][0], "kid": "k2"}],
}


def _mint(
    sub: str = ADMIN,
    *,
    typ: str = "access",
    kid: str = "k1",
    exp_offset: int = 300,
    aud: str | None = None,
    iss: str | None = None,
    private_pem: str = PRIVATE_PEM,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "user_id": sub,
        "type": typ,
        "iat": now,
        "exp": now + timedelta(seconds=exp_offset),
        "iss": iss or settings.JWT_ISSUER,
        "aud": aud or settings.JWT_AUDIENCE,
        "av": 0,
        "arv": settings.ADMIN_ROLE_VERSION,
        "role": "admin",
        "jti": f"{typ}_{sub}_{uuid.uuid4().hex}",
    }
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})


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


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")


@pytest.fixture(autouse=True)
def _stub_auth_service(monkeypatch):
    """Only the auth-version round-trip is stubbed; the token itself is still
    verified for real by the SDK."""
    from app.api.routes import admin as routes

    class AuthResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"auth_version": 0}

    class AuthClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return AuthResponse()

    monkeypatch.setattr(routes.httpx, "AsyncClient", AuthClient)


@pytest.fixture
async def admin_client(tmp_path):
    """A client on the real admin router. Auth fails before the DB is touched,
    so the session factory is a formality here."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jwks.db'}")
    async with engine.begin() as conn:
        from app.models.admin import Base

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    DatabaseManager.session_factory = factory

    async def _db_override():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = _db_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Finding 1 -- the route goes through the SDK entry point
# ---------------------------------------------------------------------------


class TestVerifierIsWiredIn:
    def test_the_route_uses_the_sdk_entry_point(self):
        # Not the two hand-wired steps any more: the SDK owns fetch, cache,
        # rotation refresh and JWKS validation.
        assert admin_routes.verify_token_with_jwks is wildframe_auth.verify_token_with_jwks
        assert not hasattr(admin_routes, "get_cached_jwks")
        assert not hasattr(admin_routes, "verify_jwt_token")

    async def test_the_sdk_rotates_a_key_without_waiting_out_the_cache_ttl(self, endpoint):
        """The regression the old hand-wiring could not fix.

        A perfectly legitimate rotation makes every token signed with the new
        key look like an unknown ``kid`` until the 300s cache expires. The
        SDK refetches once; the old code had no such path and returned 401 for
        up to five minutes after every rotation.
        """
        payload = await _decode_token(_mint())
        assert payload["sub"] == ADMIN
        assert endpoint.fetches == 1

        endpoint.jwks = ROTATED_JWKS  # auth-service publishes the new key
        rotated = await _decode_token(_mint(kid="k2"))

        assert rotated["sub"] == ADMIN
        assert endpoint.fetches == 2, "the warm cache must be refreshed once"


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

    async def test_an_http_error_status_is_503(self, endpoint):
        endpoint.raises = RuntimeError("HTTP 503")
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint())
        assert exc.value.status_code == 503

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
        """The poisoned-cache defect, seen from the client's side.

        The old code cached this body and then did ``jwks.get("keys")`` on it,
        so a JSON array raised ``AttributeError`` and surfaced as a 500 — for
        the whole TTL, re-poisoned on every refetch.
        """
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
        # Each attempt really retried: the bad body was never cached.
        assert endpoint.fetches == 3

    async def test_a_rotation_time_outage_is_503_not_a_500(self, endpoint):
        """A failed *forced* refetch is the case that used to escape as a bare
        transport error, missing the service's ``except JWTError`` entirely."""
        assert (await _decode_token(_mint()))["sub"] == ADMIN
        endpoint.raises = ConnectionError("auth-service dropped the refresh")
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(kid="k-rotated"))
        assert exc.value.status_code == 503
        assert exc.value.detail == "Token verification is unavailable"


class TestBadTokenIs401:
    async def test_an_expired_token_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(exp_offset=-10_000))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

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

    async def test_a_garbage_token_is_401(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token("not-a-jwt")
        assert exc.value.status_code == 401

    async def test_an_unknown_kid_is_401(self, endpoint):
        """A kid that is not published *and* not a valid key stays a 401 — the
        rotation refresh must not turn "unknown" into "accepted"."""
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(kid="k-never-published"))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_step_up_token_is_401_when_an_access_token_is_required(self, endpoint):
        with pytest.raises(HTTPException) as exc:
            await _decode_token(_mint(typ="admin_step_up"))
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# The same split, as the client sees it
# ---------------------------------------------------------------------------


class TestStatusCodesOverHttp:
    async def test_a_jwks_outage_reaches_the_client_as_503(self, endpoint, admin_client):
        endpoint.raises = ConnectionError("auth-service unreachable")
        response = await admin_client.get(
            "/api/v1/admin/alerts", headers={"Authorization": f"Bearer {_mint()}"}
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Token verification is unavailable"

    async def test_a_bad_token_reaches_the_client_as_401(self, endpoint, admin_client):
        response = await admin_client.get(
            "/api/v1/admin/alerts", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    async def test_a_missing_header_is_still_401(self, endpoint, admin_client):
        response = await admin_client.get("/api/v1/admin/alerts")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid Authorization header"

    async def test_a_healthy_admin_gets_past_the_token_boundary(self, endpoint, admin_client):
        """Sanity: the 503/401 split did not break the accept path."""
        await get_current_admin_id(f"Bearer {_mint()}")
        response = await admin_client.get(
            "/api/v1/admin/alerts", headers={"Authorization": f"Bearer {_mint()}"}
        )
        assert response.status_code == 200
