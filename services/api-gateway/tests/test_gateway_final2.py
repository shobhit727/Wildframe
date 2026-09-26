"""Test gateway age middleware."""

import pytest
from fastapi.responses import Response

from app.middleware import age_middleware


async def test_gateway_final2():
    """The middleware stamps a Vary header so jurisdiction caches stay correct."""
    captured = {}

    async def call_next(request):
        captured["path"] = request.url.path
        return Response(content=b"ok")

    response = await age_middleware(
        _scope_request("/maturity/film"), call_next
    )

    assert captured["path"] == "/maturity/film"
    assert response.headers["Vary"] == "X-Jurisdiction, X-Age-Verified"
    assert response.body == b"ok"


def _scope_request(path):
    from fastapi import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "client": ("1.2.3.4", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
            "root_path": "",
        }
    )
