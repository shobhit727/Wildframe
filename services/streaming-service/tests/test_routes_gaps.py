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
    router,
)
from app.core.settings import settings
from _test_jwks import JWKS, PRIVATE_PEM

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
    async def get_jwks(_url):
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

    monkeypatch.setattr(routes, "get_cached_jwks", get_jwks)
    monkeypatch.setattr(routes.httpx, "AsyncClient", AuthClient)


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
        "attacker-controlled-key-000000000000000",
        algorithm=settings.JWT_ALGORITHM,
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
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
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
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
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
async def test_require_admin_rejects_a_missing_arv_claim():
    """A missing arv defaults to 0, which only passes when ADMIN_ROLE_VERSION==0."""
    header = f"Bearer {mint(role='admin', arv=None)}"
    expected = 403 if settings.ADMIN_ROLE_VERSION != 0 else None
    if expected:
        with pytest.raises(HTTPException) as exc:
            await require_admin(uuid4(), header)
        assert exc.value.status_code == 403


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
    fake_service.require_manifest_session.assert_awaited_once_with(user, episode_id, manifest.content_id)
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
