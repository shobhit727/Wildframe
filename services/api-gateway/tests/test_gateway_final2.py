"""The age gate middleware, at the middleware layer (#1023).

What used to be here asserted that `age_middleware` let `/maturity/film`
through untouched -- it pinned the pass-through that *was* the defect, while
`check_age_gate` had 16 green unit tests and no production caller at all. The
Vary assertion is kept, on a request the gate actually admits; the
pass-through assertion is replaced by the refusal it should have been.

The ASGI-level proof that this middleware is registered lives in
test_age_gate_wiring.py. This file covers the middleware on its own.
"""

import pytest
from fastapi import Request
from fastapi.responses import Response

from app.middleware import AgeGateMiddleware


@pytest.fixture
def verified_age_claim(monkeypatch):
    """Make a bearer token verify as age-verified.

    Only the JWKS *fetch* is stubbed -- it is an HTTP call to auth-service,
    the external boundary. `verify_token_with_jwks` itself is the shared
    verifier the gateway already uses everywhere else.
    """

    async def _verify(*_args, **_kwargs):
        return {"sub": "u1", "age_verified": True, "is_minor": False}

    monkeypatch.setattr("app.middleware.verify_token_with_jwks", _verify)


def _scope_request(path, headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": raw,
            "client": ("1.2.3.4", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
            "root_path": "",
        }
    )


async def _collect(seen):
    async def call_next(request):
        seen.append(request.url.path)
        return Response(content=b"ok")

    return call_next


async def _run_gate(request, call_next):
    return await AgeGateMiddleware(app=None).dispatch(request, call_next)


async def test_gateway_final2(verified_age_claim):
    """The middleware stamps a Vary header so jurisdiction caches stay correct."""
    seen = []

    response = await _run_gate(
        _scope_request("/maturity/film", {"Authorization": "Bearer token"}),
        await _collect(seen),
    )

    assert seen == ["/maturity/film"]
    assert response.headers["Vary"] == "X-Jurisdiction, X-Age-Verified"
    assert response.body == b"ok"


@pytest.mark.parametrize("path", ["/maturity/film", "/purchase/checkout", "/content/restricted/x"])
async def test_an_unverified_restricted_request_is_refused_not_forwarded(path):
    """The old pass-through admitted exactly these requests."""
    seen = []

    response = await _run_gate(_scope_request(path), await _collect(seen))

    assert response.status_code == 403
    assert b"Age verification required" in response.body
    assert seen == [], "the request was forwarded upstream anyway"
