from app.middleware import ServiceRegistry


def test_gateway_final():
    """Every registry entry resolves to an in-network URL the proxy can dial."""
    assert ServiceRegistry.SERVICES
    for name, url in ServiceRegistry.SERVICES.items():
        assert name == name.strip() and name
        assert url.startswith("http://"), f"{name} must not be a public URL"
        # Inside the compose network every service binds its own container port.
        assert url.endswith(":8000"), f"{name} -> {url} must use the container port"
