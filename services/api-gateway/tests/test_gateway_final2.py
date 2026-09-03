"""Test gateway age middleware."""

from app.middleware.age import age_middleware


def test_gateway_final2():
    assert age_middleware is not None
