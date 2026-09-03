def test_gateway_final3():
    import pathlib

    assert pathlib.Path("app/middleware/age.py").exists()
    assert pathlib.Path("app/core/billing_proxy.py").exists()
    assert pathlib.Path("app/core/age_gate.py").exists()
