"""Tests for security headers and the gateway's authentication boundary."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from jose import jwt

from app.core.security_headers import SECURITY_HEADERS, rotation_check
from app.middleware import (
    AuthenticationMiddleware,
    LoadBalancer,
    ServiceRegistry,
    age_middleware,
    get_current_user,
    get_optional_user,
    get_shared_client,
    shared_client_lifespan,
)

SECRET = "unit-test-secret-key-at-least-32-characters-long"


def test_security_headers():
    assert "Strict-Transport-Security" in SECURITY_HEADERS


def test_rotation():
    assert rotation_check("k1") is True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _request(path="/content/api/v1/titles", headers=None, method="GET"):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw,
        "client": ("1.2.3.4", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
        "root_path": "",
    }
    from fastapi import Request

    return Request(scope)


def _token(sub="user-1", **claims):
    payload = {"sub": sub, "exp": int(time.time()) + 600}
    payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.fixture
def auth():
    return AuthenticationMiddleware(SECRET)


# ---------------------------------------------------------------------------
# AuthenticationMiddleware.verify_token
# ---------------------------------------------------------------------------


async def test_verify_token_returns_the_decoded_claims(auth):
    payload = await auth.verify_token(
        _request(headers={"authorization": f"Bearer {_token('user-9')}"})
    )
    assert payload is not None
    assert payload["sub"] == "user-9"


async def test_verify_token_is_case_insensitive_about_the_bearer_scheme(auth):
    payload = await auth.verify_token(
        _request(headers={"authorization": f"bearer {_token()}"})
    )
    assert payload is not None


async def test_verify_token_returns_none_without_an_authorization_header(auth):
    assert await auth.verify_token(_request()) is None


async def test_verify_token_rejects_a_non_bearer_scheme(auth):
    """The gateway never inspects Basic credentials; it just declines them."""
    assert await auth.verify_token(_request(headers={"authorization": "Basic dXNlcg=="})) is None


async def test_verify_token_rejects_a_malformed_authorization_header(auth):
    assert await auth.verify_token(_request(headers={"authorization": "Bearer"})) is None


async def test_verify_token_rejects_a_token_signed_with_another_key(auth):
    forged = jwt.encode(
        {"sub": "attacker", "exp": int(time.time()) + 600}, "wrong-key", algorithm="HS256"
    )
    assert await auth.verify_token(_request(headers={"authorization": f"Bearer {forged}"})) is None


async def test_verify_token_rejects_an_expired_token(auth):
    expired = jwt.encode(
        {"sub": "user-1", "exp": int(time.time()) - 60}, SECRET, algorithm="HS256"
    )
    assert await auth.verify_token(_request(headers={"authorization": f"Bearer {expired}"})) is None


async def test_verify_token_requires_an_expiry_claim(auth):
    """Expiry is mandatory even at this transparent-proxy boundary."""
    no_exp = jwt.encode({"sub": "user-1"}, SECRET, algorithm="HS256")
    assert await auth.verify_token(_request(headers={"authorization": f"Bearer {no_exp}"})) is None


async def test_verify_token_ignores_audience_because_upstream_enforces_it(auth):
    """An aud-bearing token is accepted here and validated by the backend."""
    token = _token(aud="some-other-service")
    payload = await auth.verify_token(_request(headers={"authorization": f"Bearer {token}"}))
    assert payload is not None
    assert payload["aud"] == "some-other-service"


# ---------------------------------------------------------------------------
# AuthenticationMiddleware.__call__
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/auth/register",
        "/auth/login",
        "/health",
        "/ready",
        "/gateway/health",
        "/gateway/ready",
        "/auth/login/",  # trailing slash is normalised
    ],
)
async def test_public_paths_bypass_authentication(auth, path):
    assert await auth(_request(path=path)) is None


async def test_public_prefix_paths_bypass_authentication(auth):
    assert await auth(_request(path="/auth/login/oauth")) is None


@pytest.mark.parametrize("path", ["/content/api/v1/titles", "/auth/refresh", "/admin/x"])
async def test_protected_paths_require_a_valid_token(auth, path):
    with pytest.raises(HTTPException) as exc:
        await auth(_request(path=path))
    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"
    assert "Invalid or missing" in exc.value.detail


async def test_protected_paths_return_the_claims_for_a_valid_token(auth):
    payload = await auth(
        _request(path="/content/api/v1/titles", headers={"authorization": f"Bearer {_token('u-7')}"})
    )
    assert payload["sub"] == "u-7"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
async def test_docs_paths_always_require_a_token_regardless_of_environment(
    auth, path, monkeypatch
):
    """Docs are gated in *both* environments, not just production.

    ``__call__`` reads::

        if is_docs_path and ENVIRONMENT == "production":  pass
        elif <path is in PUBLIC_PATHS>:                    return None

    ``pass`` falls through to the token check, and docs paths are absent from
    ``PUBLIC_PATHS``, so the ``elif`` never rescues them either. The
    environment comparison therefore has no observable effect: /docs is
    anonymous-unreachable everywhere. Pinned here because the branch is a no-op.
    """
    from app.core.settings import settings

    for environment in ("development", "production"):
        monkeypatch.setattr(settings, "ENVIRONMENT", environment)
        with pytest.raises(HTTPException) as exc:
            await auth(_request(path=path))
        assert exc.value.status_code == 401


async def test_the_root_path_is_normalised_before_the_public_check(auth):
    """`"/".rstrip("/")` is empty, so the `or "/"` fallback restores the root."""
    with pytest.raises(HTTPException) as exc:
        await auth(_request(path="/"))
    assert exc.value.status_code == 401


async def test_a_trailing_slash_on_a_public_path_is_normalised(auth):
    assert await auth(_request(path="/health/")) is None


# ---------------------------------------------------------------------------
# FastAPI auth dependencies
# ---------------------------------------------------------------------------


@pytest.fixture
def installed_auth(auth):
    """Install an AuthenticationMiddleware where the dependencies look it up."""
    import app.main as main

    saved = main.auth_middleware
    main.auth_middleware = auth
    try:
        yield auth
    finally:
        main.auth_middleware = saved


async def test_get_current_user_returns_the_claims(installed_auth):
    payload = await get_current_user(
        _request(headers={"authorization": f"Bearer {_token('u-11')}"})
    )
    assert payload["sub"] == "u-11"


async def test_get_current_user_raises_401_without_a_token(installed_auth):
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request())
    assert exc.value.status_code == 401
    assert exc.value.detail == "Authentication required"


async def test_get_optional_user_returns_none_for_anonymous_requests(installed_auth):
    assert await get_optional_user(_request()) is None


async def test_get_optional_user_returns_the_claims_when_present(installed_auth):
    payload = await get_optional_user(
        _request(headers={"authorization": f"Bearer {_token('u-12')}"})
    )
    assert payload["sub"] == "u-12"


async def test_the_auth_dependencies_refuse_to_run_before_startup():
    """The late import yields None until the lifespan installs the middleware."""
    import app.main as main

    saved = main.auth_middleware
    main.auth_middleware = None
    try:
        with pytest.raises(AssertionError):
            await get_current_user(_request())
        with pytest.raises(AssertionError):
            await get_optional_user(_request())
    finally:
        main.auth_middleware = saved


# ---------------------------------------------------------------------------
# shared httpx client
# ---------------------------------------------------------------------------


async def test_get_shared_client_raises_outside_the_lifespan():
    import app.middleware as mw

    saved = mw._shared_client
    mw._shared_client = None
    try:
        with pytest.raises(RuntimeError, match="not initialized"):
            get_shared_client()
    finally:
        mw._shared_client = saved


async def test_shared_client_lifespan_opens_and_closes_a_pooled_client():
    import app.middleware as mw
    from app.core.settings import settings

    async with shared_client_lifespan():
        client = get_shared_client()
        assert client is not None
        assert isinstance(client.timeout.connect, float)
        assert (
            client.timeout.connect == settings.UPSTREAM_CONNECT_TIMEOUT
        )
        assert client.timeout.read == settings.UPSTREAM_READ_TIMEOUT
        assert client.timeout.write == settings.UPSTREAM_WRITE_TIMEOUT
        assert client.timeout.pool == settings.UPSTREAM_POOL_TIMEOUT

    assert client.is_closed
    assert mw._shared_client is None


async def test_shared_client_lifespan_clears_the_global_even_if_the_body_raises():
    import app.middleware as mw

    with pytest.raises(RuntimeError, match="boom"):
        async with shared_client_lifespan():
            raise RuntimeError("boom")
    assert mw._shared_client is None


# ---------------------------------------------------------------------------
# ServiceRegistry routing
# ---------------------------------------------------------------------------


def test_route_request_splits_the_service_from_the_remaining_path():
    url, path = ServiceRegistry.route_request("/content/api/v1/titles")
    assert url == "http://content-service:8000"
    assert path == "/api/v1/titles"


def test_route_request_maps_a_bare_service_to_the_root_path():
    url, path = ServiceRegistry.route_request("/auth")
    assert url == "http://auth-service:8000"
    assert path == "/"


def test_route_request_returns_no_url_for_an_unregistered_service():
    url, path = ServiceRegistry.route_request("/nope/api")
    assert url is None
    assert path == "/api"


@pytest.mark.parametrize("path", ["/", "", "///"])
def test_route_request_never_raises_on_an_empty_path(path):
    """``"".split("/")`` is always a one-element list, so this never 500s."""
    url, remaining = ServiceRegistry.route_request(path)
    assert url is None
    assert remaining == "/"


def test_service_registry_is_immutable():
    assert len(ServiceRegistry.SERVICES) == 14
    with pytest.raises(TypeError):
        ServiceRegistry.SERVICES["evil"] = "http://evil"  # type: ignore[index]


# ---------------------------------------------------------------------------
# LoadBalancer
# ---------------------------------------------------------------------------


class _FakeHealthClient:
    def __init__(self, status_code=200, error=None):
        self.status_code = status_code
        self.error = error
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.requested.append(url)
        if self.error is not None:
            raise self.error
        return MagicMock(status_code=self.status_code)


async def test_load_balancer_returns_the_url_when_the_backend_is_healthy():
    fake = _FakeHealthClient(status_code=200)
    with patch("app.middleware.httpx.AsyncClient", return_value=fake):
        assert await LoadBalancer().get_healthy_instance("auth") == "http://auth-service:8000"
    assert fake.requested == ["http://auth-service:8000/health"]


async def test_load_balancer_returns_none_for_an_unhealthy_backend():
    with patch("app.middleware.httpx.AsyncClient", return_value=_FakeHealthClient(503)):
        assert await LoadBalancer().get_healthy_instance("auth") is None


async def test_load_balancer_returns_none_when_the_health_probe_raises():
    fake = _FakeHealthClient(error=OSError("connection refused"))
    with patch("app.middleware.httpx.AsyncClient", return_value=fake):
        assert await LoadBalancer().get_healthy_instance("auth") is None


async def test_load_balancer_short_circuits_for_an_unknown_service():
    """No health probe should be attempted for a service with no URL."""
    fake = _FakeHealthClient()
    with patch("app.middleware.httpx.AsyncClient", return_value=fake):
        assert await LoadBalancer().get_healthy_instance("not-a-service") is None
    assert fake.requested == []


# ---------------------------------------------------------------------------
# age_middleware
# ---------------------------------------------------------------------------


async def test_age_middleware_adds_a_vary_header_for_jurisdiction_caches():
    async def call_next(request):
        return Response(content=b"ok")

    response = await age_middleware(_request(), call_next)
    assert response.headers["Vary"] == "X-Jurisdiction, X-Age-Verified"
    assert response.body == b"ok"
