"""Edge-branch coverage for UserService — raises, 404/400 paths, auto-creation."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.services import UserService
from tests.test_user_service import _mock_preferences

pytestmark = pytest.mark.asyncio


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def service():
    svc = UserService(
        profile_repo=AsyncMock(),
        device_repo=AsyncMock(),
        preference_repo=AsyncMock(),
        subscription_repo=AsyncMock(),
    )
    return svc


class TestProfileErrors:
    async def test_create_profile_wraps_error_as_500(self, service, user_id):
        service.profile_repo.create.side_effect = RuntimeError("db down")
        service.profile_repo.commit.side_effect = RuntimeError("db down")

        with pytest.raises(HTTPException) as exc:
            await service.create_user_profile(user_id)

        assert exc.value.status_code == 500

    async def test_get_profile_missing_raises_404(self, service, user_id):
        service.profile_repo.get_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.get_user_profile(user_id)

        assert exc.value.status_code == 404

    async def test_update_profile_missing_raises_404(self, service, user_id):
        from app.schemas import UserProfileUpdateRequest

        service.profile_repo.update.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.update_user_profile(user_id, UserProfileUpdateRequest(display_name="X"))

        assert exc.value.status_code == 404
        service.profile_repo.commit.assert_not_awaited()

    async def test_get_complete_profile_missing_raises_404(self, service, user_id):
        service.profile_repo.get_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.get_complete_profile(user_id)

        assert exc.value.status_code == 404


class TestDeviceErrors:
    async def test_register_device_wraps_as_400(self, service, user_id):
        from app.schemas import UserDeviceRegisterRequest

        service.device_repo.create.side_effect = RuntimeError("boom")

        with pytest.raises(HTTPException) as exc:
            await service.register_device(
                user_id,
                UserDeviceRegisterRequest(device_id="d1", device_name="TV", device_type="smart_tv"),
                "1.2.3.4",
            )

        assert exc.value.status_code == 400

    async def test_update_device_missing_raises_404(self, service):
        from app.schemas import UserDeviceUpdateRequest

        service.device_repo.update.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.update_device(uuid4(), UserDeviceUpdateRequest(device_name="x"))

        assert exc.value.status_code == 404

    async def test_deactivate_device_missing_raises_404(self, service):
        service.device_repo.mark_device_inactive.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.deactivate_device(uuid4())

        assert exc.value.status_code == 404

    async def test_remove_device_success(self, service):
        service.device_repo.delete.return_value = True

        result = await service.remove_device(uuid4())

        assert result is True
        service.device_repo.commit.assert_awaited_once()

    async def test_remove_device_missing_raises_404(self, service):
        service.device_repo.delete.return_value = False

        with pytest.raises(HTTPException) as exc:
            await service.remove_device(uuid4())

        assert exc.value.status_code == 404


class TestPreferenceErrors:
    async def test_get_preferences_creates_default_when_missing(self, service, user_id):
        defaults = _mock_preferences(user_id)
        service.preference_repo.get_by_user_id.return_value = None
        service.preference_repo.create_default.return_value = defaults

        result = await service.get_preferences(user_id)

        assert result.user_id == user_id
        service.preference_repo.create_default.assert_awaited_once_with(user_id)

    async def test_update_preferences_auto_creates_then_updates(self, service, user_id):
        from app.schemas import UserPreferenceUpdateRequest

        updated = _mock_preferences(user_id)
        service.preference_repo.get_by_user_id.return_value = None
        service.preference_repo.create_default.return_value = updated
        service.preference_repo.update.return_value = updated

        result = await service.update_preferences(
            user_id, UserPreferenceUpdateRequest(theme="dark")
        )

        assert result.user_id == user_id
        service.preference_repo.create_default.assert_awaited_once_with(user_id)
        service.preference_repo.commit.assert_awaited_once()

    async def test_update_preferences_missing_after_update_raises_404(self, service, user_id):
        from app.schemas import UserPreferenceUpdateRequest

        service.preference_repo.get_by_user_id.return_value = MagicMock()
        service.preference_repo.update.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.update_preferences(user_id, UserPreferenceUpdateRequest(theme="dark"))

        assert exc.value.status_code == 404


class TestSubscriptionErrors:
    async def test_get_subscription_missing_raises_404(self, service, user_id):
        service.subscription_repo.get_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.get_subscription(user_id)

        assert exc.value.status_code == 404

    async def test_upgrade_subscription_missing_raises_404(self, service, user_id):
        service.subscription_repo.update_tier.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.upgrade_subscription(user_id, "premium")

        assert exc.value.status_code == 404
        service.subscription_repo.commit.assert_not_awaited()


class TestOnboardingError:
    async def test_mark_onboarding_missing_raises_404(self, service, user_id):
        service.profile_repo.mark_onboarding_complete.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.mark_onboarding_complete(user_id)

        assert exc.value.status_code == 404
