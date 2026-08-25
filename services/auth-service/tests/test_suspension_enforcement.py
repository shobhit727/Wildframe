"""Regression tests: moderation enforcement at the auth boundary.

Suspended/banned accounts must be rejected at login AND refresh (the
user.moderated consumer flips users.is_active; these tests pin the
service-level behavior the consumer relies on).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.security import PasswordManager, TokenManager
from app.schemas import UserLoginRequest
from app.services import AuthService


@pytest.fixture
def auth_service():
    return AuthService(
        user_repo=AsyncMock(),
        token_repo=AsyncMock(),
        audit_repo=AsyncMock(),
        password_manager=PasswordManager(),
        token_manager=TokenManager(),
    )


@pytest.mark.usefixtures("_no_redis_rate_limit")
@pytest.mark.asyncio
class TestSuspendedAccountEnforcement:
    async def test_login_rejects_inactive_account(self, auth_service):
        """is_active=False (suspended via user.moderated) -> 403 at login."""
        user = MagicMock()
        user.email = "suspended@wildframe.com"
        user.is_active = False
        user.locked_until = None
        auth_service.user_repo.get_by_email = AsyncMock(return_value=user)

        with pytest.raises(HTTPException) as exc:
            await auth_service.login(
                UserLoginRequest(email="suspended@wildframe.com", password="whatever"),
                ip_address="127.0.0.1",
            )
        assert exc.value.status_code == 403
        assert "suspended" in exc.value.detail.lower()

    async def test_login_allows_active_account_past_password_check(self, auth_service):
        """is_active=True proceeds past the suspension gate (fails later at
        password verify with a bad secret, but NOT with 'suspended')."""
        user = MagicMock()
        user.email = "active@wildframe.com"
        user.is_active = True
        user.locked_until = None
        user.login_attempts = 0
        user.password_hash = "not-a-real-hash"
        user.login_attempts = 0
        auth_service.user_repo.get_by_email = AsyncMock(return_value=user)
        auth_service.user_repo.increment_login_attempts = AsyncMock(return_value=user)

        with pytest.raises(HTTPException) as exc:
            await auth_service.login(
                UserLoginRequest(email="active@wildframe.com", password="whatever"),
                ip_address="127.0.0.1",
            )
        assert "suspended" not in str(exc.value.detail).lower()

    async def test_refresh_rejects_inactive_account(self, auth_service):
        """Suspended accounts cannot mint new tokens via refresh."""
        from app.services import HTTPException

        user = MagicMock()
        user.id = "u-1"
        user.email = "suspended@wildframe.com"
        user.is_active = False
        auth_service.token_manager.verify_refresh_token = AsyncMock(return_value="u-1")
        auth_service.user_repo.get_by_id = AsyncMock(return_value=user)

        with pytest.raises(HTTPException) as exc:
            await auth_service.refresh_token("some-refresh-token")
        assert exc.value.status_code == 403
        assert "suspended" in exc.value.detail.lower()

    async def test_locked_account_still_locked(self, auth_service):
        """Existing lockout behavior is unchanged by the suspension gate."""
        user = MagicMock()
        user.email = "locked@wildframe.com"
        user.is_active = True
        user.login_attempts = 5
        user.locked_until = datetime.now(UTC) + timedelta(minutes=10)
        auth_service.user_repo.get_by_email = AsyncMock(return_value=user)

        with pytest.raises(HTTPException) as exc:
            await auth_service.login(
                UserLoginRequest(email="locked@wildframe.com", password="whatever"),
                ip_address="127.0.0.1",
            )
        assert exc.value.status_code == 429
