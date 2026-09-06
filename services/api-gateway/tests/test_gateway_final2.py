"""Test gateway age middleware."""

from app.middleware import age_middleware


def test_gateway_final2():
    assert age_middleware is not None
