"""Pytest configuration and fixtures for recommendation-service tests."""

import pytest

# @pytest.fixture(autouse=True)
# def mock_redis_cache():
#     """Mock Redis cache functions for all tests to avoid cross-test interference."""
#     with patch("app.services._cache_get", new=AsyncMock(return_value=None)):
#         with patch("app.services._cache_set", new=AsyncMock()):
#             with patch("app.services._cache_invalidate", new=AsyncMock()):
#                 yield


@pytest.fixture(autouse=True)
def reset_catalog_client():
    """Reset the global catalog client between tests."""
    import app.services as services_module

    original = services_module._catalog_client
    original_class = services_module._catalog_client_class
    services_module._catalog_client = None
    services_module._catalog_client_class = None
    yield
    services_module._catalog_client = original
    services_module._catalog_client_class = original_class
