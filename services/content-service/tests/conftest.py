import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def _mock_auth_introspection():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("app.api.routes.httpx.AsyncClient", return_value=mock_client):
            yield
