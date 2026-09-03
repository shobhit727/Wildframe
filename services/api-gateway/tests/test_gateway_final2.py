def test_gateway_final2():
    from app.middleware.age import age_middleware
    assert age_middleware is not None
