"""The age gate must be **wired**, not merely unit-tested (#1023).

`check_age_gate` had 16 green unit tests and zero callers in production:
`age_middleware` was a pass-through that never called it and was never
registered in `create_app`. Every test in this file therefore drives the real
ASGI app (``app.main.app``, the object ``create_app()`` returns) and asserts on
the HTTP response, because the only failure mode that matters here is
reachability -- a unit test cannot see it.

Each test also asserts **which requests reached the upstream at all**
(``captured["called"]``), so "refused" cannot be satisfied by a downstream
failure that merely happens to share a status code.

Policy pinned here (owner decision still open -- see the module docstring in
``app/core/age_gate.py`` and the #1023 write-up):

* an age-restricted path with **no** age claim is refused (block-on-absent);
* an age claim supplied as a **client header** is not proof and is refused --
  the gateway strips ``X-Age-Verified`` / ``X-Is-Minor`` at the edge;
* only a **verified bearer token** carrying the claim opens the gate, and the
  value the gateway forwards upstream is the one it derived, never the
  client's.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.api.gateway_routes import get_optional_user
from app.main import app

# A verified-token fixture. The JWKS verifier is mocked because it is the
# *external* boundary (an HTTP call to auth-service); the logic under test is
# the wiring and the policy, neither of which this replaces.
VERIFIED_TOKEN = "header.payload.signature"


class _FakeUpstreamResponse:
    """The subset of ``httpx.Response`` the proxy route actually touches."""

    def __init__(self, body: bytes = b'{"ok": true}'):
        self.status_code = 200
        self._body = body
        self.headers = httpx.Headers({"content-type": "application/json"})
        self.raw = [(b"content-type", b"application/json")]

    async def __aenter__(self) -> "_FakeUpstreamResponse":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def aiter_raw(self):
        yield self._body


class _FakeUpstreamClient:
    """Records the forwarded call so a test can prove what upstream was told."""

    def __init__(self, captured: dict):
        self._captured = captured

    def stream(self, method, url, headers, content):
        self._captured["called"] = True
        self._captured["method"] = method
        self._captured["url"] = url
        self._captured["upstream_path"] = url.split(":8000", 1)[-1]
        self._captured["headers"] = {k.lower(): v for k, v in headers.items()}
        return _FakeUpstreamResponse()


@pytest.fixture
def gateway(monkeypatch):
    """A TestClient over the real app, with only the *upstream* stubbed out.

    The proxy target and the rate limiter are replaced; the middleware stack,
    the router and the age gate are the real ones.
    """
    captured: dict = {}
    monkeypatch.setattr(
        "app.api.gateway_routes.get_shared_client", lambda: _FakeUpstreamClient(captured)
    )
    app.dependency_overrides[get_optional_user] = lambda: {"sub": "test-user"}
    try:
        with TestClient(app, base_url="http://test") as client:
            # Set after the lifespan: startup rebuilds `main.rate_limiter`, and
            # a real one would try to reach a Redis that is not running here.
            monkeypatch.setattr(main, "rate_limiter", None)
            yield client, captured
    finally:
        app.dependency_overrides.clear()


def _with_claims(monkeypatch, claims: dict | None) -> None:
    """Make every bearer token verify as ``claims`` (None = verification fails)."""

    async def _fake_verify(*_args, **_kwargs):
        return claims

    monkeypatch.setattr("app.middleware.verify_token_with_jwks", _fake_verify)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {VERIFIED_TOKEN}"}


# ---------------------------------------------------------------------------
# Reachability -- the defect #1023 describes
# ---------------------------------------------------------------------------


def test_unverified_request_to_a_restricted_path_is_refused_by_the_running_app(gateway):
    """THE regression test. Before the fix this reached the upstream and 200'd.

    Nothing here stubs the gate: if the middleware is not registered in
    `create_app`, the request is proxied and this assertion fails.
    """
    client, captured = gateway

    resp = client.get("/content/restricted/film-1")

    assert resp.status_code == 403, "age gate did not run on the request path"
    assert "Age verification required" in resp.json()["detail"]
    assert captured.get("called") is None, "the request reached the upstream anyway"


@pytest.mark.parametrize("path", ["/maturity/film", "/purchase/checkout", "/content/restricted"])
def test_every_declared_prefix_is_actually_gated(gateway, path):
    """A prefix that is only enforced in a unit test is not enforced."""
    client, captured = gateway

    assert client.get(path).status_code == 403
    assert captured.get("called") is None


def test_a_sibling_route_sharing_a_prefix_is_still_not_gated(gateway):
    """#879's boundary fix must survive being wired in (#879, #1023)."""
    client, captured = gateway

    assert client.get("/content/restrictedxyz").status_code == 200
    assert captured["called"] is True


# ---------------------------------------------------------------------------
# Policy: absent vs forged
# ---------------------------------------------------------------------------


def test_a_client_supplied_age_header_is_not_proof(gateway):
    """Conservative reading of "absent vs forged": a self-asserted header 403s."""
    client, captured = gateway

    resp = client.get("/maturity/film", headers={"X-Age-Verified": "true", "X-Is-Minor": "false"})

    assert resp.status_code == 403
    assert captured.get("called") is None


def test_a_client_supplied_age_header_is_never_forwarded_upstream(gateway, monkeypatch):
    """A forged header must not reach a service that might trust it."""
    client, captured = gateway
    _with_claims(monkeypatch, {"sub": "u1", "age_verified": True, "is_minor": False})

    resp = client.get(
        "/content/restricted/film-1",
        headers={**_auth(), "X-Age-Verified": "true", "X-Is-Minor": "true"},
    )

    assert resp.status_code == 200
    assert captured["headers"]["x-age-verified"] == "true"
    assert captured["headers"]["x-is-minor"] == "false", "the client's X-Is-Minor won"


def test_a_valid_token_without_an_age_claim_is_refused(gateway, monkeypatch):
    """Block-on-absent: a real token is not an age verification."""
    client, captured = gateway
    _with_claims(monkeypatch, {"sub": "u1", "type": "access"})

    resp = client.get("/content/restricted/film-1", headers=_auth())

    assert resp.status_code == 403
    assert captured.get("called") is None


def test_an_unverifiable_token_is_refused(gateway, monkeypatch):
    """JWKS unavailable / bad token -> no claim -> blocked, never an outage."""
    client, captured = gateway
    _with_claims(monkeypatch, None)

    assert client.get("/content/restricted/film-1", headers=_auth()).status_code == 403
    assert captured.get("called") is None


# ---------------------------------------------------------------------------
# Policy: what does open the gate
# ---------------------------------------------------------------------------


def test_a_verified_age_claim_reaches_the_upstream(gateway, monkeypatch):
    client, captured = gateway
    _with_claims(monkeypatch, {"sub": "u1", "age_verified": True, "is_minor": False})

    resp = client.get("/content/restricted/film-1", headers=_auth())

    assert resp.status_code == 200
    assert captured["called"] is True
    assert captured["headers"]["x-age-verified"] == "true"
    assert captured["headers"]["x-is-minor"] == "false"


def test_a_verified_minor_is_forwarded_as_a_minor(gateway, monkeypatch):
    client, captured = gateway
    _with_claims(monkeypatch, {"sub": "u1", "age_verified": True, "is_minor": True})

    assert client.get("/content/restricted/checkout", headers=_auth()).status_code == 200
    assert captured["headers"]["x-is-minor"] == "true"


def test_a_false_age_claim_in_the_token_is_refused(gateway, monkeypatch):
    """``age_verified: false`` must not be read as "the header is absent"."""
    client, _ = gateway
    _with_claims(monkeypatch, {"sub": "u1", "age_verified": False})

    assert client.get("/content/restricted/film-1", headers=_auth()).status_code == 403


# ---------------------------------------------------------------------------
# The gate must not leak onto unrestricted routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/content/catalog", "/search/api/v1/items"])
def test_unrestricted_paths_are_untouched_by_the_gate(gateway, path):
    client, captured = gateway

    assert client.get(path).status_code == 200
    # The service prefix is stripped by ServiceRegistry before proxying.
    assert captured["upstream_path"] == "/" + path.split("/", 2)[2]
    # No age claim was derived, so none is asserted upstream.
    assert "x-age-verified" not in captured["headers"]


def test_the_gate_leaves_the_gateways_own_routes_alone(gateway):
    """``/health`` is served by the gateway itself and must still answer."""
    client, captured = gateway

    assert client.get("/health").json() == {"status": "ok"}
    assert captured.get("called") is None


def test_a_verified_claim_on_an_unrestricted_path_injects_nothing(gateway, monkeypatch):
    """Deriving headers only where they are read keeps the proxy transparent."""
    client, captured = gateway
    _with_claims(monkeypatch, {"sub": "u1", "age_verified": True, "is_minor": True})

    assert client.get("/content/catalog", headers=_auth()).status_code == 200
    assert "x-age-verified" not in captured["headers"]
