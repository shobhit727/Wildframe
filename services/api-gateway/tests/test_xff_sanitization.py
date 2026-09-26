import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.settings import settings
from app.middleware import HeaderSanitizerMiddleware


def _make_request(headers=None, client_host="9.9.9.9", client_port=12345):
    headers = headers or {}
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": (client_host, client_port) if client_host else None,
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
        "root_path": "",
    }
    return Request(scope)


async def _run_middleware(request, monkeypatch=None):
    async def call_next(req: Request):
        xff = req.headers.get("x-forwarded-for")
        xri = req.headers.get("x-real-ip")
        return JSONResponse({"x-forwarded-for": xff, "x-real-ip": xri})

    def app(scope, receive, send):
        return None

    mw = HeaderSanitizerMiddleware(app)
    return await mw.dispatch(request, call_next)


@pytest.mark.asyncio
async def test_attacker_xff_not_preserved(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    req = _make_request(
        headers={"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3", "x-real-ip": "1.1.1.1"},
        client_host="9.9.9.9",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "9.9.9.9"
    assert body["x-real-ip"] == "9.9.9.9"
    assert "1.1.1.1" not in body["x-forwarded-for"]
    assert "2.2.2.2" not in body["x-forwarded-for"]


@pytest.mark.asyncio
async def test_legitimate_client_ip(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    req = _make_request(headers={}, client_host="1.2.3.4")
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "1.2.3.4"
    assert body["x-real-ip"] == "1.2.3.4"


@pytest.mark.asyncio
async def test_forged_multi_hop_xff_discarded(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    req = _make_request(
        headers={"x-forwarded-for": "203.0.113.1, 198.51.100.5, 192.0.2.9"},
        client_host="5.5.5.5",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "5.5.5.5"
    assert "203.0.113.1" not in body["x-forwarded-for"]


@pytest.mark.asyncio
async def test_trust_proxy_disabled_ignores_xff(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.1")
    req = _make_request(
        headers={"x-forwarded-for": "203.0.113.10"},
        client_host="10.0.0.1",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_trust_proxy_enabled_trusted_peer_extracts_real_ip(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.1")
    req = _make_request(
        headers={"x-forwarded-for": "203.0.113.10"},
        client_host="10.0.0.1",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "203.0.113.10"
    assert body["x-real-ip"] == "203.0.113.10"


@pytest.mark.asyncio
async def test_trust_proxy_enabled_forged_multi_hop_returns_last_real(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.1")
    req = _make_request(
        headers={"x-forwarded-for": "1.1.1.1, 2.2.2.2, 203.0.113.10"},
        client_host="10.0.0.1",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "203.0.113.10"
    assert "1.1.1.1" not in body["x-forwarded-for"]
    assert body["x-forwarded-for"] != "1.1.1.1"


@pytest.mark.asyncio
async def test_trust_proxy_enabled_untrusted_peer_ignores_xff(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.1")
    req = _make_request(
        headers={"x-forwarded-for": "203.0.113.10"},
        client_host="9.9.9.9",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "9.9.9.9"
    assert body["x-forwarded-for"] != "203.0.113.10"


@pytest.mark.asyncio
async def test_trust_proxy_enabled_cidr(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/24")
    req = _make_request(
        headers={"x-forwarded-for": "198.51.100.7"},
        client_host="10.0.0.5",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "198.51.100.7"


@pytest.mark.asyncio
async def test_trust_proxy_enabled_no_trusted_list_uses_last_xff(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    req = _make_request(
        headers={"x-forwarded-for": "203.0.113.5"},
        client_host="10.0.0.1",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "203.0.113.5"


@pytest.mark.asyncio
async def test_trust_proxy_enabled_no_xff_falls_back_to_socket(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.1")
    req = _make_request(headers={}, client_host="10.0.0.1")
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_x_real_ip_mirrors_xff(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    req = _make_request(
        headers={"x-forwarded-for": "9.9.9.9", "x-real-ip": "9.9.9.9"},
        client_host="1.1.1.1",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == body["x-real-ip"] == "1.1.1.1"


@pytest.mark.asyncio
async def test_trusted_lb_chain_with_multiple_proxies(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.1, 10.0.0.2")
    req = _make_request(
        headers={"x-forwarded-for": "1.1.1.1, 10.0.0.2, 203.0.113.9"},
        client_host="10.0.0.1",
    )
    resp = await _run_middleware(req)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "203.0.113.9"
    assert "1.1.1.1" not in body["x-forwarded-for"]


@pytest.mark.asyncio
async def test_unknown_client(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    req = _make_request(headers={"x-forwarded-for": "1.1.1.1"}, client_host=None)
    scope = req.scope
    scope["client"] = None
    req2 = Request(scope)
    resp = await _run_middleware(req2)
    import json

    body = json.loads(resp.body)
    assert body["x-forwarded-for"] == "unknown"
    assert body["x-real-ip"] == "unknown"


# ---------------------------------------------------------------------------
# Unit-level coverage of the trusted-proxy parsing helpers
# ---------------------------------------------------------------------------


def test_trusted_proxies_splits_a_comma_separated_string(monkeypatch):
    from app.middleware import _trusted_proxies

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", " 10.0.0.1 , ,10.0.0.0/8 ,")
    assert _trusted_proxies() == ["10.0.0.1", "10.0.0.0/8"]


def test_trusted_proxies_accepts_a_list_of_cidrs(monkeypatch):
    """The setting is typed ``str``, but a list is handled defensively."""
    from app.middleware import _trusted_proxies

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", [" 10.0.0.0/8 ", " 10.0.0.1 ", ""])
    assert _trusted_proxies() == ["10.0.0.0/8", "10.0.0.1"]


@pytest.mark.parametrize("raw", [123, 1.5, object()])
def test_trusted_proxies_returns_empty_for_a_non_string_non_list_value(
    monkeypatch, raw
):
    from app.middleware import _trusted_proxies

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", raw)
    assert _trusted_proxies() == []


def test_trusted_proxies_treats_none_as_empty(monkeypatch):
    from app.middleware import _trusted_proxies

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", None)
    assert _trusted_proxies() == []


def test_is_trusted_ip_rejects_a_malformed_candidate():
    from app.middleware import _is_trusted_ip

    assert _is_trusted_ip("not-an-ip", ["10.0.0.1"]) is False


def test_is_trusted_ip_matches_an_exact_address():
    from app.middleware import _is_trusted_ip

    assert _is_trusted_ip("10.0.0.1", ["10.0.0.1"]) is True
    assert _is_trusted_ip("10.0.0.2", ["10.0.0.1"]) is False


def test_is_trusted_ip_matches_a_cidr_containing_the_candidate():
    from app.middleware import _is_trusted_ip

    assert _is_trusted_ip("10.0.0.5", ["10.0.0.0/24"]) is True
    assert _is_trusted_ip("10.0.1.5", ["10.0.0.0/24"]) is False


def test_is_trusted_ip_skips_a_malformed_cidr_and_keeps_looking():
    """A bad entry must not abort the whole list."""
    from app.middleware import _is_trusted_ip

    assert _is_trusted_ip("10.0.0.5", ["not/a/cidr", "10.0.0.0/24"]) is True


def test_is_trusted_ip_skips_blank_entries():
    from app.middleware import _is_trusted_ip

    assert _is_trusted_ip("10.0.0.5", ["   ", "", "10.0.0.0/24"]) is True
    assert _is_trusted_ip("10.0.0.5", ["   "]) is False


def test_is_trusted_ip_does_not_match_a_non_ip_trust_entry_against_an_ip():
    """A garbage trust entry is compared verbatim and rejected.

    The literal-comparison fallback can never *succeed*: reaching it requires a
    parseable candidate IP, and ``entry == ip_str`` would then make ``entry``
    parseable too. Pinned here so the fallback is not removed by accident.
    """
    from app.middleware import _is_trusted_ip

    assert _is_trusted_ip("10.0.0.5", ["lb.internal"]) is False
    assert _is_trusted_ip("10.0.0.5", ["lb.internal", "10.0.0.5"]) is True


def test_is_trusted_ip_returns_false_for_an_empty_trust_list():
    from app.middleware import _is_trusted_ip

    assert _is_trusted_ip("10.0.0.1", []) is False


def test_derive_real_ip_ignores_an_all_separator_xff_header(monkeypatch):
    from app.middleware import _derive_real_ip

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    # Everything splits away, so there is no usable hop to adopt.
    assert _derive_real_ip("10.0.0.1", " , , ") == "10.0.0.1"


def test_derive_real_ip_skips_invalid_hops_and_takes_the_first_valid(monkeypatch):
    from app.middleware import _derive_real_ip

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    assert _derive_real_ip("10.0.0.1", "203.0.113.9, garbage") == "203.0.113.9"


def test_derive_real_ip_ignores_garbage_when_no_trusted_list_is_configured(
    monkeypatch,
):
    from app.middleware import _derive_real_ip

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    assert _derive_real_ip("10.0.0.1", "garbage") == "10.0.0.1"


def test_derive_real_ip_skips_trusted_hops_inside_the_chain(monkeypatch):
    from app.middleware import _derive_real_ip

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    # Walked right-to-left: 10.0.0.2 is itself trusted, so keep going left.
    assert _derive_real_ip("10.0.0.1", "203.0.113.9, 10.0.0.2") == "203.0.113.9"


def test_derive_real_ip_falls_back_to_socket_when_every_hop_is_unusable(
    monkeypatch,
):
    from app.middleware import _derive_real_ip

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    assert _derive_real_ip("10.0.0.1", "not-an-ip") == "10.0.0.1"


def test_derive_real_ip_returns_empty_xff_as_the_socket_peer(monkeypatch):
    from app.middleware import _derive_real_ip

    monkeypatch.setattr(settings, "TRUST_PROXY", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    assert _derive_real_ip("10.0.0.1", None) == "10.0.0.1"
    assert _derive_real_ip("10.0.0.1", "") == "10.0.0.1"


def test_derive_real_ip_returns_socket_ip_verbatim_when_proxying_is_disabled(
    monkeypatch,
):
    from app.middleware import _derive_real_ip

    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "10.0.0.0/8")
    assert _derive_real_ip("10.0.0.1", "203.0.113.9") == "10.0.0.1"


async def test_sanitizer_drops_client_supplied_identity_headers(monkeypatch):
    """#314/#522/#625: X-User-* and correlation ids are rebuilt, never trusted."""
    monkeypatch.setattr(settings, "TRUST_PROXY", False)
    req = _make_request(
        headers={
            "x-user-id": "attacker",
            "x-user-email": "attacker@evil.test",
            "x-user-roles": "admin",
            "x-request-id": "client-supplied",
            "x-correlation-id": "client-supplied",
            "x-keep": "kept",
        },
        client_host="1.2.3.4",
    )
    resp = await _run_middleware(req)
    # The helper only reports the rewritten values; the strip set is asserted
    # directly against the module constant.
    from app.middleware import _STRIP_HEADERS

    for name in (
        "x-user-id",
        "x-user-email",
        "x-user-roles",
        "x-request-id",
        "x-correlation-id",
        "x-forwarded-for",
        "x-real-ip",
    ):
        assert name in _STRIP_HEADERS
    assert "x-keep" not in _STRIP_HEADERS
    assert resp.status_code == 200
