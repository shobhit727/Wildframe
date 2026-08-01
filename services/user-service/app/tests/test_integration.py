"""Integration tests for User Service."""
import pytest
import pytest_asyncio
import json
from uuid import UUID
from httpx import AsyncClient
from app.main import app
from app.services import UserService
from app.repositories import (
    UserProfileRepository, UserDeviceRepository,
    UserPreferenceRepository, UserSubscriptionProfileRepository
)
from app.schemas import (
    UserProfileUpdateRequest, UserDeviceRegisterRequest,
    UserPreferenceUpdateRequest
)


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing."""
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def user_service(db_session):
    """UserService instance with test DB."""
    return UserService(
        profile_repo=UserProfileRepository(db_session),
        device_repo=UserDeviceRepository(db_session),
        preference_repo=UserPreferenceRepository(db_session),
        subscription_repo=UserSubscriptionProfileRepository(db_session),
    )


class TestUserProfileIntegration:
    """Integration tests for user profiles."""

    async def test_create_user_profile(self, user_service, db_session):
        """Test creating a user profile."""
        from uuid import uuid4
        user_id = uuid4()
        
        profile = await user_service.create_user_profile(user_id)
        
        assert profile.user_id == user_id
        assert profile.completed_onboarding is False
        assert profile.profile_completeness == 0
        
        # Verify in DB
        from app.repositories import UserProfileRepository
        repo = UserProfileRepository(db_session)
        db_profile = await repo.get_by_user_id(user_id)
        assert db_profile is not None
        assert db_profile.user_id == user_id

    async def test_get_user_profile(self, user_service, db_session):
        """Test getting user profile."""
        from uuid import uuid4
        user_id = uuid4()
        
        await user_service.create_user_profile(user_id)
        profile = await user_service.get_user_profile(user_id)
        
        assert profile.user_id == user_id

    async def test_update_user_profile(self, user_service, db_session):
        """Test updating user profile."""
        from uuid import uuid4
        from app.schemas import UserProfileUpdateRequest
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        update_request = UserProfileUpdateRequest(
            bio="Updated bio",
            country="US",
            language="en-US",
        )
        
        updated = await user_service.update_user_profile(user_id, update_request)
        
        assert updated.bio == "Updated bio"
        assert updated.country == "US"
        assert updated.language == "en-US"

    async def test_get_complete_profile(self, user_service, db_session):
        """Test getting complete profile with all related data."""
        from uuid import uuid4
        from app.schemas import UserProfileCompleteResponse
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        complete = await user_service.get_complete_profile(user_id)
        
        assert isinstance(complete.profile.user_id, UUID)
        assert isinstance(complete.devices, list)
        assert isinstance(complete.preferences, object)
        assert isinstance(complete.subscription, object)


class TestUserDeviceIntegration:
    """Integration tests for user devices."""

    async def test_register_device(self, user_service, db_session):
        """Test registering a device."""
        from uuid import uuid4
        from app.schemas import UserDeviceRegisterRequest
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        device_request = UserDeviceRegisterRequest(
            device_id="device-123",
            device_name="Test Device",
            device_type="web",
            os_name="macOS",
            browser_name="Chrome",
        )
        
        device = await user_service.register_device(user_id, device_request, "127.0.0.1")
        
        assert device.device_id == "device-123"
        assert device.device_name == "Test Device"
        assert device.device_type == "web"
        assert device.is_active is True

    async def test_get_user_devices(self, user_service, db_session):
        """Test getting user devices."""
        from uuid import uuid4
        from app.schemas import UserDeviceRegisterRequest
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        device_request = UserDeviceRegisterRequest(
            device_id="device-456",
            device_name="Second Device",
            device_type="ios",
        )
        
        await user_service.register_device(user_id, device_request, "127.0.0.1")
        devices = await user_service.get_user_devices(user_id)
        
        assert len(devices) == 1
        assert devices[0].device_id == "device-456"


class TestUserPreferenceIntegration:
    """Integration tests for user preferences."""

    async def test_get_preferences(self, user_service, db_session):
        """Test getting default preferences."""
        from uuid import uuid4
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        prefs = await user_service.get_preferences(user_id)
        
        assert prefs.theme == "dark"
        assert prefs.language == "en-US"
        assert prefs.autoplay is True

    async def test_update_preferences(self, user_service, db_session):
        """Test updating preferences."""
        from uuid import uuid4
        from app.schemas import UserPreferenceUpdateRequest
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        update_request = UserPreferenceUpdateRequest(
            theme="light",
            language="fr-FR",
            autoplay=False,
        )
        
        updated = await user_service.update_preferences(user_id, update_request)
        
        assert updated.theme == "light"
        assert updated.language == "fr-FR"
        assert updated.autoplay is False


class TestUserSubscriptionIntegration:
    """Integration tests for user subscriptions."""

    async def test_get_subscription(self, user_service, db_session):
        """Test getting subscription."""
        from uuid import uuid4
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        sub = await user_service.get_subscription(user_id)
        
        assert sub.subscription_tier == "free"
        assert sub.max_concurrent_streams == 1
        assert sub.can_download is False

    async def test_upgrade_subscription(self, user_service, db_session):
        """Test upgrading subscription."""
        from uuid import uuid4
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        upgraded = await user_service.upgrade_subscription(user_id, "premium")
        
        assert upgraded.subscription_tier == "premium"
        assert upgraded.max_concurrent_streams == 4
        assert upgraded.can_download is True
        assert upgraded.can_use_4k is True
        assert upgraded.ad_free is True


class TestUserOnboardingIntegration:
    """Integration tests for onboarding."""

    async def test_mark_onboarding_complete(self, user_service, db_session):
        """Test marking onboarding complete."""
        from uuid import uuid4
        
        user_id = uuid4()
        await user_service.create_user_profile(user_id)
        
        profile = await user_service.mark_onboarding_complete(user_id)
        
        assert profile.completed_onboarding is True
        assert profile.profile_completeness == 100