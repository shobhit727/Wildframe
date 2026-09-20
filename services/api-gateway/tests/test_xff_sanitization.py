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
