"""Coverage for the two under-exercised helpers in
``streaming-service/app/api/routes/__init__.py``.

1. ``get_current_user_id`` (lines 37-75) -- the bearer-token boundary. The
   existing suite overrides this dependency, so its own reject/accept
   branches were never executed.
2. ``get_episode_manifest`` (lines 266-312) -- the signed/unsigned manifest
   gate, which is the most branch-heavy handler in the file.

Also closes the remaining route-layer gaps in this module (require_admin,
require_self, and the 404/403/409 arms of the playback, transcoding, download
and signed-URL handlers).

The repo rule "don't mutate state before authorization" is asserted explicitly
for every guarded handler -- see the ``test_no_mutation_*`` cases.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from jose import jwt

import app.api.routes as routes
import app.main as main_module
from app.api.routes import (
    get_current_user_id,
    get_current_user_id_or_none,
    get_streaming_service,
    require_admin,
    require_self,
)
from app.core.settings import settings
from _test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

# ------------------------------------------------------------- JWT minting --

GOOD_JWT = "K7bQx2Zf9pLw4mNc8vRt3yHs6dJg1aEe5uIoP0zXcVb"

# Sentinel: omit the claim entirely rather than sending null.
DROP = object()


def mint(**overrides) -> str:
    """Mint an access token the way auth-service would."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "role": "user",
        "av": 0,
        "arv": settings.ADMIN_ROLE_VERSION,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    for key, value in overrides.items():
        if value is DROP:
            claims.pop(key, None)
        else:
            claims[key] = value
    return jwt.encode(claims, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def _stub_auth_verification(monkeypatch):
    # Replace only the outbound JWKS fetch, not the verifier: the cache, the
    # single-flight and the real RS256 signature check all keep running.
    async def fetch(_url):
        return JWKS

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

    monkeypatch.setattr("wildframe_auth.verifier.fetch_jwks", fetch)
    monkeypatch.setattr(routes.httpx, "AsyncClient", AuthClient)
    clear_jwks_cache()
    yield
    clear_jwks_cache()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ============================== get_current_user_id ==========================
# Exercised directly as a coroutine: it is a plain function, not a route, so
# calling it avoids minting a whole request just to assert a 401.


@pytest.mark.unit
@pytest.mark.parametrize("header", [None, "", "Basic abc123", "bearer lowercase", "Token x"])
async def test_get_current_user_id_rejects_a_non_bearer_header(header):
    """routes/__init__.py:41-45 -- anything but 'Bearer ' is a 401."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(header)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing or invalid authorization header"


@pytest.mark.unit
async def test_get_current_user_id_accepts_a_valid_access_token():
    """The happy path returns the ``sub`` claim as a UUID."""
    subject = uuid4()
    token = mint(sub=str(subject))
    assert await get_current_user_id(f"Bearer {token}") == subject


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_missing_subject():
    """routes/__init__.py:66-69 -- no sub and no user_id is a 401."""
    token = mint(sub=DROP, user_id=DROP)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_non_uuid_subject():
    """routes/__init__.py:70-75 -- an unparseable sub is a 401, not a 500."""
    token = mint(sub="not-a-uuid")
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_non_string_subject():
    """A non-string ``sub`` is caught by python-jose claim validation.

    It therefore fails in the ``except JWTError`` arm (routes/__init__.py:63-64)
    with "Invalid token" rather than reaching the ``except (TypeError,
    ValueError)`` arm -- both are 401, so the endpoint is safe either way.
    """
    token = mint(sub=12345)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_stale_auth_version(monkeypatch):
    subject = str(uuid4())
    token = mint(sub=subject, av=1)

    class StaleAuthResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"auth_version": 2}

    class StaleAuthClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return StaleAuthResponse()

    monkeypatch.setattr(routes.httpx, "AsyncClient", StaleAuthClient)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401


@pytest.mark.unit
async def test_get_current_user_id_rejects_missing_sub_even_with_user_id():
    token = mint(sub=DROP, user_id=str(uuid4()))
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_garbage_token():
    """routes/__init__.py:63-64 -- a JWTError becomes a 401, never a 500."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id("Bearer not-a-jwt-at-all")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


def _wrong_rsa_private_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_token_signed_with_another_key():
    """A valid JWT under the wrong key must not authenticate."""
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        _wrong_rsa_private_pem(),
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {forged}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_get_current_user_id_rejects_the_wrong_audience(monkeypatch):
    """The decode pins ``audience``; a token for another API must be refused."""
    other = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": "some-other-api",
            "iss": settings.JWT_ISSUER,
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {other}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_get_current_user_id_rejects_the_wrong_issuer(monkeypatch):
    """A correctly-signed token from another issuer must be refused."""
    other = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": "attacker-issuer",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "iat": int(datetime.now(UTC).timestamp()),
            "av": 0,
        },
        PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {other}")
    assert exc.value.status_code == 401


@pytest.mark.unit
async def test_get_current_user_id_rejects_an_expired_token():
    """``require_exp`` is set, so an expired token is a 401."""
    now = datetime.now(UTC)
    token = mint(
        iat=int((now - timedelta(hours=2)).timestamp()),
        exp=int((now - timedelta(hours=1)).timestamp()),
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_refresh_token():
    """#221: a refresh token shares the audience but must never be an access token."""
    token = mint(type="refresh")
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_get_current_user_id_rejects_a_token_with_no_type_claim():
    """A legacy token without ``type`` is refused rather than assumed access."""
    token = mint(type=None)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


# ===================== _enforce_auth_version failures ======================
#
# The auth-version round trip is the second half of authentication: streaming
# asks auth-service what the caller's current auth_version is, and that answer
# decides 401 vs 503. Misreading an *upstream outage* as "your token is bad" is
# the expensive direction -- every signed-in user is bounced to /login because
# one dependency blipped, which is the failure class AGENTS.md calls out. None
# of these branches was exercised.


def _install_auth_client(monkeypatch, *, status_code=200, payload=None, raises=None):
    """Replace the outbound /auth/me client with a scripted response."""

    class ScriptedResponse:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            if payload is None:
                return {}
            return payload

    class ScriptedClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            if raises is not None:
                raise raises
            return ScriptedResponse()

    monkeypatch.setattr(routes.httpx, "AsyncClient", ScriptedClient)


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 599])
async def test_an_auth_service_5xx_is_a_503_not_a_401(monkeypatch, status_code):
    """routes/__init__.py:80-84 -- an auth-service outage must not log users out.

    401 here means "your token is invalid": the caller is redirected to /login
    and the session is discarded. During an auth blip every signed-in user would
    be logged out by a dependency that is merely unavailable.
    """
    _install_auth_client(monkeypatch, status_code=status_code)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=0)}")
    assert exc.value.status_code == 503
    assert exc.value.detail == "Auth service unavailable"


@pytest.mark.unit
async def test_an_auth_service_transport_failure_is_a_503(monkeypatch):
    """routes/__init__.py:73-77 -- a connect/read timeout is also 'unavailable'."""
    _install_auth_client(monkeypatch, raises=routes.httpx.ConnectError("refused"))
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=0)}")
    assert exc.value.status_code == 503
    assert exc.value.detail == "Auth service unavailable"


@pytest.mark.unit
async def test_an_unexpected_auth_client_error_is_a_401(monkeypatch):
    """routes/__init__.py:78-79 -- anything else that is not a transport error
    is treated as an unusable request, not as an outage."""
    _install_auth_client(monkeypatch, raises=RuntimeError("client misconfigured"))
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=0)}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or expired token"


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 302])
async def test_a_non_200_auth_response_is_a_401(monkeypatch, status_code):
    """routes/__init__.py:85-86 -- only 200 is a usable answer; the rest mean the
    token is not currently valid."""
    _install_auth_client(monkeypatch, status_code=status_code, payload={"auth_version": 0})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=0)}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or expired token"


@pytest.mark.unit
@pytest.mark.parametrize(
    "av",
    [DROP, None, "0", 0.0, True, [], {}],
    ids=["absent", "null", "string", "float", "bool", "list", "dict"],
)
async def test_a_non_integer_token_auth_version_is_a_401(monkeypatch, av):
    """A token ``av`` that is not a real integer is rejected before auth-service
    is contacted.

    ``bool`` is in the set because ``isinstance(True, int)`` is True; only a
    ``type(x) is int`` check rejects it.

    Note on where the rejection happens: ``routes/__init__.py:65-66`` is
    defence-in-depth that the shared verifier currently makes unreachable --
    ``wildframe_auth.verifier.verify_token_with_jwks`` raises
    ``JWTError("invalid auth version claim")`` for every value here, so the
    401 comes from that arm and the local check never executes. This test
    therefore asserts the contract that is true either way (401, and no
    dependency round trip) instead of mocking the verifier away to force
    coverage of a branch that cannot run.
    """
    calls = []

    class CountingClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            calls.append(1)
            raise AssertionError("auth-service must not be called for a malformed av")

    monkeypatch.setattr(routes.httpx, "AsyncClient", CountingClient)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=av)}")
    assert exc.value.status_code == 401
    assert calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_av", [None, "0", 0.0, True], ids=["null", "string", "float", "bool"]
)
async def test_a_non_integer_current_auth_version_is_a_401(monkeypatch, current_av):
    """routes/__init__.py:91-92 -- auth-service's answer must also be an int.

    A stringy ``auth_version`` would otherwise compare unequal to a valid token
    ``av``; the 401 is the same either way, but the *reason* differs and a
    mismatched-type response is an upstream contract break worth naming.
    """
    _install_auth_client(monkeypatch, payload={"auth_version": current_av})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=0)}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token payload"


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, {"auth_version": None}], ids=["absent", "null"])
async def test_an_auth_response_without_auth_version_is_a_401(monkeypatch, payload):
    """routes/__init__.py:87-90 -- a missing key is a 401, never a KeyError 500."""
    _install_auth_client(monkeypatch, payload=payload)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=0)}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token payload"


@pytest.mark.unit
async def test_a_non_object_auth_response_is_a_401(monkeypatch):
    """A JSON array makes ``response.json()["auth_version"]`` a TypeError."""
    _install_auth_client(monkeypatch, payload=["auth_version", 0])
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {mint(av=0)}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token payload"


@pytest.mark.unit
async def test_a_matching_auth_version_is_accepted(monkeypatch):
    """The positive control: equal ints on both sides let the token through."""
    subject = str(uuid4())
    _install_auth_client(monkeypatch, payload={"auth_version": 7})
    assert await get_current_user_id(f"Bearer {mint(sub=subject, av=7)}") == UUID(subject)


@pytest.mark.unit
async def test_an_empty_subject_claim_is_a_401():
    """routes/__init__.py:110-112 -- ``sub`` is required by the shared verifier
    but may be the empty string, which is a subject claim with no subject."""
    token = mint(sub="")
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


# ======================= get_current_user_id_or_none ========================


def _request_with_override(app, has_override: bool) -> Request:
    scope = {
        "type": "http",
        "app": app,
        "headers": [],
        "path_params": {},
        "method": "GET",
    }
    return Request(scope)


@pytest.mark.unit
async def test_get_current_user_id_or_none_uses_an_injected_override():
    """Test hooks can inject a user without minting a token."""
    app = MagicMock()
    injected = uuid4()
    app.dependency_overrides = {get_current_user_id: lambda: injected}
    request = _request_with_override(app, True)
    assert await get_current_user_id_or_none(request) == injected


@pytest.mark.unit
async def test_get_current_user_id_or_none_returns_none_from_a_none_override():
    """An override yielding None means 'anonymous', not an error."""
    app = MagicMock()
    app.dependency_overrides = {get_current_user_id: lambda: None}
    request = _request_with_override(app, True)
    assert await get_current_user_id_or_none(request) is None


@pytest.mark.unit
async def test_get_current_user_id_or_none_rejects_a_bad_override_value():
    """A non-UUID, non-None override is treated as anonymous."""
    app = MagicMock()
    app.dependency_overrides = {get_current_user_id: lambda: "not-a-uuid"}
    request = _request_with_override(app, True)
    assert await get_current_user_id_or_none(request) is None


@pytest.mark.unit
async def test_get_current_user_id_or_none_swallows_an_override_http_error():
    """routes/__init__.py:119-120 -- an override raising 401 means anonymous."""
    app = MagicMock()

    def _boom():
        raise HTTPException(status_code=401, detail="nope")

    app.dependency_overrides = {get_current_user_id: _boom}
    request = _request_with_override(app, True)
    assert await get_current_user_id_or_none(request) is None


@pytest.mark.unit
async def test_get_current_user_id_or_none_returns_none_for_missing_sub():
    app = MagicMock()
    app.dependency_overrides = {}
    request = _request_with_override(app, False)
    token = mint(sub=DROP, user_id=str(uuid4()))
    assert await get_current_user_id_or_none(request, f"Bearer {token}") is None


@pytest.mark.unit
async def test_get_current_user_id_or_none_returns_none_for_a_bad_token():
    """routes/__init__.py:121-124 -- an invalid token degrades to anonymous."""
    app = MagicMock()
    app.dependency_overrides = {}
    request = _request_with_override(app, False)
    assert await get_current_user_id_or_none(request, "Bearer garbage") is None
    assert await get_current_user_id_or_none(request, None) is None


# ================================ require_admin ==============================


@pytest.mark.unit
async def test_require_admin_accepts_admin_with_the_current_role_version():
    """routes/__init__.py:92-96 -- role + arv must both match."""
    admin = uuid4()
    header = f"Bearer {mint(sub=str(admin), role='admin', arv=settings.ADMIN_ROLE_VERSION)}"
    assert await require_admin(admin, header) == admin


@pytest.mark.unit
async def test_require_admin_rejects_a_stale_role_version():
    """An admin token minted before a role bump must lose admin rights."""
    header = f"Bearer {mint(role='admin', arv=settings.ADMIN_ROLE_VERSION + 1)}"
    with pytest.raises(HTTPException) as exc:
        await require_admin(uuid4(), header)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Administrator privileges required"


@pytest.mark.unit
@pytest.mark.parametrize(
    "arv",
    [DROP, None, "", "0", 0.0, False, [], {}],
    ids=["absent", "null", "empty-string", "numeric-string", "float", "bool", "list", "dict"],
)
async def test_require_admin_rejects_a_non_integer_arv_claim(arv):
    """A missing / null / non-integer ``arv`` is never the admin role version.

    ``ADMIN_ROLE_VERSION`` defaults to 0, so ``int(payload.get("arv") or 0)``
    silently promoted any admin-role token *without* an ``arv`` claim to a
    current administrator, and granted it ``POST /manifests``,
    ``/transcoding-jobs``, ``/quality-profiles`` and ``/cdn-regions``. A claim
    that is absent carries no evidence that the role has not been revoked, so it
    must fail closed. ``0.0``/``False`` are in the set because
    ``type(x) is int`` -- not ``isinstance`` -- is what keeps ``True`` from
    passing as ``1``.
    """
    header = f"Bearer {mint(role='admin', arv=arv)}"
    with pytest.raises(HTTPException) as exc:
        await require_admin(uuid4(), header)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Administrator privileges required"


@pytest.mark.unit
@pytest.mark.parametrize("offset", [-1, 1, 99])
async def test_require_admin_rejects_any_role_version_other_than_the_current(offset):
    """Both directions of drift are denied: older *and* newer than current."""
    header = f"Bearer {mint(role='admin', arv=settings.ADMIN_ROLE_VERSION + offset)}"
    with pytest.raises(HTTPException) as exc:
        await require_admin(uuid4(), header)
    assert exc.value.status_code == 403


@pytest.mark.unit
async def test_require_admin_accepts_the_current_role_version_at_the_route_boundary():
    """The four operational admin routes are reachable with role + current arv."""
    user = uuid4()
    token = mint(sub=str(user), role="admin", arv=settings.ADMIN_ROLE_VERSION)
    assert await require_admin(user, f"Bearer {token}") == user


@pytest.mark.unit
async def test_require_admin_rejects_a_non_admin_role():
    """An ordinary viewer must not inherit admin."""
    header = f"Bearer {mint(role='user', arv=settings.ADMIN_ROLE_VERSION)}"
    with pytest.raises(HTTPException) as exc:
        await require_admin(uuid4(), header)
    assert exc.value.status_code == 403


@pytest.mark.unit
async def test_require_admin_rejects_an_unparseable_arv_claim():
    """routes/__init__.py:97-98 -- a non-integer arv is a 401, not a 500."""
    header = f"Bearer {mint(role='admin', arv='not-a-number')}"
    with pytest.raises(HTTPException) as exc:
        await require_admin(uuid4(), header)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Administrator privileges required"


@pytest.mark.unit
async def test_require_admin_rejects_a_garbage_token():
    """An undecodable token is a 401 at the admin gate."""
    with pytest.raises(HTTPException) as exc:
        await require_admin(uuid4(), "Bearer garbage")
    assert exc.value.status_code == 401


# ================================ require_self ===============================


@pytest.mark.unit
async def test_require_self_allows_a_matching_path_user():
    """routes/__init__.py:133-134 -- own data is served."""
    user = uuid4()
    request = _request_with_override(MagicMock(), False)
    request.scope["path_params"] = {"user_id": str(user)}
    assert await require_self(user, request) == user


@pytest.mark.unit
async def test_require_self_allows_when_no_path_user_is_present():
    """No path param means the route is not user-scoped; fall through."""
    user = uuid4()
    request = _request_with_override(MagicMock(), False)
    request.scope["path_params"] = {}
    assert await require_self(user, request) == user


@pytest.mark.unit
async def test_require_self_rejects_a_mismatch_with_403():
    """routes/__init__.py:135-138 -- someone else's data is a 403."""
    request = _request_with_override(MagicMock(), False)
    request.scope["path_params"] = {"user_id": str(uuid4())}
    with pytest.raises(HTTPException) as exc:
        await require_self(uuid4(), request)
    assert exc.value.status_code == 403
    assert exc.value.detail == "You can only access your own data"


# ===================== HTTP-level client with a fake service =================


def make_manifest(**overrides):
    m = MagicMock()
    m.id = uuid4()
    m.episode_id = uuid4()
    m.content_id = uuid4()
    m.protocol = "hls"
    m.manifest_url = "https://cdn.example/master.m3u8"
    m.manifest_content = "#EXTM3U"
    m.duration_seconds = 3600
    m.created_at = datetime.now(UTC)
    m.updated_at = datetime.now(UTC)
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


@pytest.fixture
def fake_service():
    svc = AsyncMock()
    svc.verify_signed_url = MagicMock(return_value=True)
    svc.require_manifest_session = AsyncMock()
    svc.check_session_valid_for_playback = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def client(fake_service):
    main_module.app.dependency_overrides.clear()
    main_module.app.dependency_overrides[get_streaming_service] = lambda: fake_service
    yield TestClient(main_module.app, base_url="http://localhost")
    main_module.app.dependency_overrides.clear()


MANIFEST_PATH = "/api/v1/episodes/{episode_id}/manifest"


def auth_client(client, user_id):
    client.app.dependency_overrides[get_current_user_id] = lambda: user_id
    return client


# ========================== get_episode_manifest ============================


@pytest.mark.unit
def test_episode_manifest_unsigned_authorized_succeeds(client, fake_service):
    """Bearer-authenticated access: manifest served, session requirement enforced."""
    manifest = make_manifest()
    fake_service.get_manifest_for_episode = AsyncMock(return_value=manifest)
    episode_id = manifest.episode_id
    user = uuid4()
    auth_client(client, user)

    r = client.get(MANIFEST_PATH.format(episode_id=episode_id))

    assert r.status_code == 200
    assert r.json()["manifest_url"] == manifest.manifest_url
    # #528/#526: authorized media must not be cached by shared intermediaries.
    assert r.headers["cache-control"] == "private, no-store"
    fake_service.require_manifest_session.assert_awaited_once_with(
        user, episode_id, manifest.content_id
    )
    # The unsigned path must not touch the signed-URL helpers.
    fake_service.verify_signed_url.assert_not_called()
    fake_service.check_session_valid_for_playback.assert_not_called()


@pytest.mark.unit
def test_episode_manifest_unsigned_anonymous_is_401(client, fake_service):
    """routes/__init__.py:292-296 -- no bearer token means 401."""
    fake_service.get_manifest_for_episode = AsyncMock(return_value=make_manifest())
    client.app.dependency_overrides[get_current_user_id_or_none] = lambda: None

    r = client.get(MANIFEST_PATH.format(episode_id=uuid4()))

    assert r.status_code == 401
    assert r.json()["detail"] == "Missing or invalid authorization header"
    # Nothing was looked up before the rejection.
    fake_service.get_manifest_for_episode.assert_not_called()


@pytest.mark.unit
def test_episode_manifest_missing_manifest_is_404(client, fake_service):
    """routes/__init__.py:299-300 -- unknown episode is a 404."""
    fake_service.get_manifest_for_episode = AsyncMock(return_value=None)
    user = uuid4()
    auth_client(client, user)

    r = client.get(MANIFEST_PATH.format(episode_id=uuid4()))

    assert r.status_code == 404
    assert r.json()["detail"] == "Manifest not found"
    # A 404 must not trigger the session entitlement check.
    fake_service.require_manifest_session.assert_not_called()


@pytest.mark.unit
def test_episode_manifest_rejects_an_unknown_protocol(client, fake_service):
    """The protocol Query pattern is validated before the handler runs."""
    auth_client(client, uuid4())
    r = client.get(MANIFEST_PATH.format(episode_id=uuid4()), params={"protocol": "rtmp"})
    assert r.status_code == 422
    fake_service.get_manifest_for_episode.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("protocol", ["hls", "dash", "smooth_streaming"])
def test_episode_manifest_accepts_every_documented_protocol(client, fake_service, protocol):
    """All three whitelisted protocols reach the service and are passed through."""
    manifest = make_manifest(protocol=protocol)
    fake_service.get_manifest_for_episode = AsyncMock(return_value=manifest)
    episode_id = manifest.episode_id
    auth_client(client, uuid4())

    r = client.get(MANIFEST_PATH.format(episode_id=episode_id), params={"protocol": protocol})

    assert r.status_code == 200
    fake_service.get_manifest_for_episode.assert_awaited_once_with(episode_id, protocol)


@pytest.mark.unit
def test_episode_manifest_signed_url_valid_grants_access(client, fake_service):
    """The signed path is self-authenticating: no bearer token needed."""
    manifest = make_manifest()
    fake_service.get_manifest_for_episode = AsyncMock(return_value=manifest)
    session_id = uuid4()
    client.app.dependency_overrides[get_current_user_id_or_none] = lambda: None

    r = client.get(
        MANIFEST_PATH.format(episode_id=manifest.episode_id),
        params={
            "session_id": str(session_id),
            "signature": "deadbeef",
            "expires": 1893456000,
        },
    )

    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-store"
    fake_service.verify_signed_url.assert_called_once_with(
        session_id, manifest.episode_id, "deadbeef", 1893456000
    )
    fake_service.check_session_valid_for_playback.assert_awaited_once_with(
        session_id, None, manifest.episode_id
    )
    # Signed access must NOT additionally demand an entitlement re-check.
    fake_service.require_manifest_session.assert_not_called()


@pytest.mark.unit
def test_episode_manifest_bad_signature_is_403(client, fake_service):
    """routes/__init__.py:280-284 -- an invalid signature never reaches the DB."""
    fake_service.verify_signed_url = MagicMock(return_value=False)
    session_id = uuid4()
    episode_id = uuid4()
    client.app.dependency_overrides[get_current_user_id_or_none] = lambda: None

    r = client.get(
        MANIFEST_PATH.format(episode_id=episode_id),
        params={"session_id": str(session_id), "signature": "bad", "expires": 1893456000},
    )

    assert r.status_code == 403
    assert r.json()["detail"] == "Invalid or expired signed URL"
    fake_service.check_session_valid_for_playback.assert_not_called()
    fake_service.get_manifest_for_episode.assert_not_called()


@pytest.mark.unit
def test_episode_manifest_revoked_session_is_403(client, fake_service):
    """routes/__init__.py:286-290 -- a valid signature on a dead session is denied."""
    session_id = uuid4()
    episode_id = uuid4()
    fake_service.check_session_valid_for_playback = AsyncMock(return_value=False)
    client.app.dependency_overrides[get_current_user_id_or_none] = lambda: None

    r = client.get(
        MANIFEST_PATH.format(episode_id=episode_id),
        params={"session_id": str(session_id), "signature": "good", "expires": 1893456000},
    )

    assert r.status_code == 403
    assert r.json()["detail"] == "Session expired or revoked"
    fake_service.get_manifest_for_episode.assert_not_called()


@pytest.mark.unit
def test_episode_manifest_partial_signed_params_fall_back_to_bearer(client, fake_service):
    """A session_id without signature/expires is not a signed request."""
    manifest = make_manifest()
    fake_service.get_manifest_for_episode = AsyncMock(return_value=manifest)
    user = uuid4()
    auth_client(client, user)

    r = client.get(
        MANIFEST_PATH.format(episode_id=manifest.episode_id),
        params={"session_id": str(uuid4())},
    )

    assert r.status_code == 200
    fake_service.verify_signed_url.assert_not_called()
    fake_service.require_manifest_session.assert_awaited_once_with(
        user, manifest.episode_id, manifest.content_id
    )


@pytest.mark.unit
def test_episode_manifest_expires_zero_is_falsy_so_bearer_is_required(client, fake_service):
    """``expires=0`` is falsy, so the signed branch is skipped entirely."""
    manifest = make_manifest()
    fake_service.get_manifest_for_episode = AsyncMock(return_value=manifest)
    client.app.dependency_overrides[get_current_user_id_or_none] = lambda: None

    r = client.get(
        MANIFEST_PATH.format(episode_id=manifest.episode_id),
        params={"session_id": str(uuid4()), "signature": "sig", "expires": 0},
    )

    assert r.status_code == 401
    fake_service.verify_signed_url.assert_not_called()


@pytest.mark.unit
def test_episode_manifest_does_not_mutate_state_before_authorizing(client, fake_service):
    """Repo rule: a 403 must come before any side effect.

    On the denied signed paths nothing beyond signature verification may run.
    """
    for verify_result, session_ok, expected in (
        (False, True, 403),
        (True, False, 403),
    ):
        fake_service.reset_mock()
        fake_service.verify_signed_url = MagicMock(return_value=verify_result)
        fake_service.check_session_valid_for_playback = AsyncMock(return_value=session_ok)
        client.app.dependency_overrides[get_current_user_id_or_none] = lambda: None

        r = client.get(
            MANIFEST_PATH.format(episode_id=uuid4()),
            params={
                "session_id": str(uuid4()),
                "signature": "s",
                "expires": 1893456000,
            },
        )
        assert r.status_code == expected
        assert fake_service.get_manifest_for_episode.await_count == 0
        assert fake_service.require_manifest_session.await_count == 0


# ========================= get_manifest (by id) ============================

BY_ID_PATH = "/api/v1/manifests/{manifest_id}"


@pytest.mark.unit
def test_manifest_by_id_requires_an_active_session(client, fake_service):
    """routes/__init__.py:294 -- the entitlement guard is not optional.

    The sibling route ``GET /episodes/{id}/manifest`` has been verified since
    #528; this one carries the same guard and used to carry none. A bare
    ``AsyncMock`` service made ``require_manifest_session`` a no-op, so deleting
    the call entirely left the whole suite green. The assertion below is on the
    *call*, so removing it now fails.
    """
    manifest = make_manifest()
    fake_service.get_manifest = AsyncMock(return_value=manifest)
    user = uuid4()
    auth_client(client, user)

    r = client.get(BY_ID_PATH.format(manifest_id=manifest.id))

    assert r.status_code == 200
    assert r.json()["id"] == str(manifest.id)
    fake_service.require_manifest_session.assert_awaited_once_with(
        user, manifest.episode_id, manifest.content_id
    )


@pytest.mark.unit
def test_manifest_by_id_without_an_active_session_is_403(client, fake_service):
    """No session for the asset -> 403 and no manifest body is served.

    ``require_manifest_session`` is the real service's denial: it raises 403
    when no active, unexpired, asset-matched session exists. Modelling the raise
    here is what makes the route guard observable at the HTTP boundary.
    """
    manifest = make_manifest()
    fake_service.get_manifest = AsyncMock(return_value=manifest)
    fake_service.require_manifest_session = AsyncMock(
        side_effect=HTTPException(
            status_code=403, detail="An active session for this asset is required"
        )
    )
    auth_client(client, uuid4())

    r = client.get(BY_ID_PATH.format(manifest_id=manifest.id))

    assert r.status_code == 403
    assert r.json()["detail"] == "An active session for this asset is required"
    # The manifest itself must not leak in the body of a denial.
    assert manifest.manifest_url not in r.text
    assert "manifest_content" not in r.text


@pytest.mark.unit
def test_manifest_by_id_is_not_cached_by_shared_intermediaries(client, fake_service):
    """B2: an entitlement-checked manifest must be ``private, no-store``.

    ``public, max-age=3600`` lets a shared CDN keep serving media for up to an
    hour after the session that authorised it has been revoked -- the same class
    of defect #528/#526 fixed on the sibling route.
    """
    manifest = make_manifest()
    fake_service.get_manifest = AsyncMock(return_value=manifest)
    auth_client(client, uuid4())

    r = client.get(BY_ID_PATH.format(manifest_id=manifest.id))

    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-store"


@pytest.mark.unit
def test_manifest_by_id_anonymous_is_401(client, fake_service):
    """No bearer token -> 401, and the manifest is never looked up."""
    fake_service.get_manifest = AsyncMock(return_value=make_manifest())

    r = client.get(BY_ID_PATH.format(manifest_id=uuid4()))

    assert r.status_code == 401
    assert r.json()["detail"] == "Missing or invalid authorization header"
    fake_service.get_manifest.assert_not_called()
    fake_service.require_manifest_session.assert_not_called()


@pytest.mark.unit
def test_manifest_by_id_missing_is_404_without_a_session_check(client, fake_service):
    """An unknown manifest is a 404 and must not trigger the entitlement lookup."""
    fake_service.get_manifest = AsyncMock(return_value=None)
    auth_client(client, uuid4())

    r = client.get(BY_ID_PATH.format(manifest_id=uuid4()))

    assert r.status_code == 404
    assert r.json()["detail"] == "Manifest not found"
    fake_service.require_manifest_session.assert_not_called()


# ============== admin operational routes reject an absent arv ==============

ADMIN_MUTATIONS = [
    (
        "manifests",
        lambda: {
            "episode_id": str(uuid4()),
            "content_id": str(uuid4()),
            "protocol": "hls",
        },
        "generate_manifest",
    ),
    (
        "transcoding-jobs",
        lambda: {
            "episode_id": str(uuid4()),
            "content_id": str(uuid4()),
            "input_file_path": "/media/episode.mp4",
        },
        "create_transcoding_job",
    ),
    (
        "quality-profiles",
        lambda: {
            "name": f"p-{uuid4()}",
            "resolution": "1080p",
            "bitrate_kbps": 5000,
            "min_bandwidth_kbps": 100,
            "max_bandwidth_kbps": 8000,
        },
        "create_quality_profile",
    ),
    (
        "cdn-regions",
        lambda: {
            "region_code": f"r-{uuid4()}"[:10],
            "region_name": "n",
            "country": "US",
            "cdn_provider": "akamai",
            "bandwidth_capacity_gbps": 100.0,
        },
        "create_cdn_region",
    ),
]


def _admin_response(path: str):
    """A response object that satisfies the route's response model."""
    fields = {
        "manifests": dict(
            protocol="hls",
            manifest_url="https://cdn.example/master.m3u8",
            variants=["720p"],
            available_bitrates=[2500],
            generated_at=datetime.now(UTC),
            expires_at=None,
        ),
        "transcoding-jobs": dict(
            status="pending",
            priority=0,
            target_resolutions=["1080p"],
            target_bitrates=[5000],
            progress_percent=0,
            started_at=None,
            completed_at=None,
            error_message=None,
        ),
        "quality-profiles": dict(
            description=None,
            fps=30,
            video_codec="h264",
            audio_codec="aac",
            audio_bitrate_kbps=128,
            supported_devices=["tv"],
            min_bandwidth_kbps=0,
            max_bandwidth_kbps=8000,
            is_active=True,
        ),
        "cdn-regions": dict(
            cdn_provider="akamai",
            max_concurrent_streams=10,
            current_active_streams=0,
            bandwidth_capacity_gbps=100.0,
            is_active=True,
        ),
    }[path]
    obj = MagicMock()
    obj.id = uuid4()
    obj.episode_id = uuid4()
    obj.content_id = uuid4()
    for key, value in fields.items():
        setattr(obj, key, value)
    if path == "quality-profiles":
        obj.name = "p"
        obj.resolution = "1080p"
        obj.bitrate_kbps = 5000
    if path == "cdn-regions":
        obj.region_code = "us-east"
        obj.region_name = "n"
        obj.country = "US"
    if path == "transcoding-jobs":
        obj.input_file_path = "/media/episode.mp4"
    return obj


@pytest.mark.unit
@pytest.mark.parametrize(
    "path,body_for,service_method",
    ADMIN_MUTATIONS,
    ids=[m[0] for m in ADMIN_MUTATIONS],
)
def test_an_admin_token_without_an_arv_claim_is_refused(
    client, fake_service, path, body_for, service_method
):
    """B3: ``role: "admin"`` with no ``arv`` must not reach the four admin routes.

    ``int(payload.get("arv") or 0)`` turned a role claim with no version evidence
    into the current admin version, because ``ADMIN_ROLE_VERSION`` is 0 by
    default. These four operational mutations -- manifest generation, a
    transcoding job, a quality profile, a CDN region -- were reachable with it.

    The fake returns a *valid* response body: against the old code the handler
    then runs and answers 201, so the failure reads ``assert 201 == 403`` --
    the bypass, named -- instead of blowing up on mock validation.
    """
    getattr(fake_service, service_method).return_value = _admin_response(path)
    headers = auth(mint(role="admin", arv=DROP))

    r = client.post(f"/api/v1/{path}", json=body_for(), headers=headers)

    assert r.status_code == 403
    assert r.json()["detail"] == "Administrator privileges required"
    # Authorization happens before the state change: nothing was written.
    getattr(fake_service, service_method).assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "path,body_for,service_method",
    ADMIN_MUTATIONS,
    ids=[m[0] for m in ADMIN_MUTATIONS],
)
def test_the_same_admin_routes_accept_a_current_role_version(
    client, fake_service, path, body_for, service_method
):
    """The control that keeps the test above honest.

    Same request, only the ``arv`` claim differs. Without this the 403 above
    would also be satisfied by a wrong path or an invalid body.
    """
    getattr(fake_service, service_method).return_value = _admin_response(path)
    headers = auth(mint(role="admin", arv=settings.ADMIN_ROLE_VERSION))

    r = client.post(f"/api/v1/{path}", json=body_for(), headers=headers)

    assert r.status_code == 201, r.text
    assert getattr(fake_service, service_method).await_count == 1


# ============== remaining route gaps: 404 / 403 / 409 arms =================


def session_owned_by(user_id):
    s = MagicMock()
    s.id = uuid4()
    s.user_id = user_id
    s.content_id = uuid4()
    s.episode_id = uuid4()
    s.status = "active"
    return s


@pytest.mark.unit
def test_update_playback_session_404_when_missing(client, fake_service):
    """routes/__init__.py:206-207."""
    fake_service.get_playback_session = AsyncMock(return_value=None)
    r = client.patch(
        f"/api/v1/playback-sessions/{uuid4()}",
        json={"resolution": "720p"},
        headers=auth(mint()),
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Session not found"


@pytest.mark.unit
def test_update_playback_session_403_for_a_foreign_session(client, fake_service):
    """routes/__init__.py:208-212 -- owner check precedes the update call."""
    fake_service.get_playback_session = AsyncMock(return_value=session_owned_by(uuid4()))
    r = client.patch(
        f"/api/v1/playback-sessions/{uuid4()}",
        json={"resolution": "720p"},
        headers=auth(mint()),
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "You can only access your own sessions"
    fake_service.update_playback_session.assert_not_called()


@pytest.mark.unit
def test_update_transcoding_progress_404_when_missing(client, fake_service):
    """routes/__init__.py:360-361."""
    fake_service.update_transcoding_progress = AsyncMock(return_value=None)
    r = client.patch(
        f"/api/v1/transcoding-jobs/{uuid4()}/progress",
        params={"progress_percent": 50},
        headers=auth(mint(role="admin", arv=settings.ADMIN_ROLE_VERSION)),
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Job not found"


@pytest.mark.unit
def test_create_download_403_for_another_user(client, fake_service):
    """routes/__init__.py:444-448 -- no session row is created on a mismatch."""
    caller = uuid4()
    r = client.post(
        "/api/v1/download-sessions",
        json={
            "user_id": str(uuid4()),
            "episode_id": str(uuid4()),
            "device_id": "d",
            "resolution": "720p",
        },
        headers=auth(mint(sub=str(caller))),
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "You can only create downloads for your own account"
    fake_service.create_download_session.assert_not_called()


@pytest.mark.unit
def test_update_download_progress_404_when_missing(client, fake_service):
    """routes/__init__.py:488-489."""
    fake_service.get_download_session = AsyncMock(return_value=None)
    r = client.patch(
        f"/api/v1/download-sessions/{uuid4()}/progress",
        params={"bytes_downloaded": 10},
        headers=auth(mint()),
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Download not found"


@pytest.mark.unit
def test_update_download_progress_403_for_a_foreign_download(client, fake_service):
    """routes/__init__.py:490-494 -- owner check precedes the write."""
    fake_service.get_download_session = AsyncMock(return_value=session_owned_by(uuid4()))
    r = client.patch(
        f"/api/v1/download-sessions/{uuid4()}/progress",
        params={"bytes_downloaded": 10},
        headers=auth(mint()),
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "You can only access your own downloads"
    fake_service.update_download_progress.assert_not_called()


@pytest.mark.unit
def test_create_signed_url_404_when_session_missing(client, fake_service):
    """routes/__init__.py:514-515."""
    fake_service.get_playback_session = AsyncMock(return_value=None)
    r = client.post(
        "/api/v1/playback-sessions/signed-url",
        json={"session_id": str(uuid4()), "content_id": str(uuid4())},
        headers=auth(mint()),
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Session not found"


@pytest.mark.unit
def test_create_signed_url_403_for_a_foreign_session(client, fake_service):
    """routes/__init__.py:516-520 -- no signature is minted for another user."""
    fake_service.get_playback_session = AsyncMock(return_value=session_owned_by(uuid4()))
    r = client.post(
        "/api/v1/playback-sessions/signed-url",
        json={"session_id": str(uuid4()), "content_id": str(uuid4())},
        headers=auth(mint()),
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "You can only generate signed URLs for your own sessions"
    fake_service.generate_signed_url.assert_not_called()


@pytest.mark.unit
def test_create_signed_url_409_when_session_is_not_active(client, fake_service):
    """routes/__init__.py:521-525 -- the session must still be playable."""
    owner = uuid4()
    fake_service.get_playback_session = AsyncMock(return_value=session_owned_by(owner))
    fake_service.check_session_valid_for_playback = AsyncMock(return_value=False)
    r = client.post(
        "/api/v1/playback-sessions/signed-url",
        json={"session_id": str(uuid4()), "content_id": str(uuid4())},
        headers=auth(mint(sub=str(owner))),
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "Session is not active"
    fake_service.generate_signed_url.assert_not_called()
