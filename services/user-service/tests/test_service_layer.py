"""Success-path tests for `app.services.UserService` and the update schemas.

`tests/test_user_service.py` and `tests/test_user_service_edges.py` already
cover the 404/error branches thoroughly. What they leave out are the two
happy paths that actually assemble data:

* `get_complete_profile` (profile + devices + preferences + subscription in
  one response), and
* `update_device` (mutation followed by a commit).

`UserProfileUpdateRequest.reject_null_nonnullable_fields` is also covered here:
it is a request-shape guard that a 200-response test never reaches.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.repositories import (
    UserDeviceRepository,
    UserPreferenceRepository,
    UserProfileRepository,
    UserSubscriptionProfileRepository,
)
from app.schemas import UserDeviceUpdateRequest, UserProfileUpdateRequest
from app.services import UserService


@pytest_asyncio.fixture
async def service(tmp_path):
    """A real UserService over a real aiosqlite schema."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/service.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield UserService(
            UserProfileRepository(session),
            UserDeviceRepository(session),
            UserPreferenceRepository(session),
            UserSubscriptionProfileRepository(session),
        )
    await engine.dispose()


# ---------------------------------------------------------------------------
# get_complete_profile
# ---------------------------------------------------------------------------


async def test_get_complete_profile_returns_none_before_provisioning(service: UserService):
    user_id = uuid4()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await service.get_complete_profile(user_id)

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "User not found"


async def test_get_complete_profile_aggregates_every_related_row(service: UserService):
    user_id = uuid4()
    await service.create_user_profile(user_id)
    device = await service.register_device(
        user_id,
        _device_request("dev-1", "Living-room TV"),
        "203.0.113.9",
    )
    await service.update_preferences(user_id, _preference_update(theme="light"))

    complete = await service.get_complete_profile(user_id)

    assert complete.profile.user_id == user_id
    assert [d.id for d in complete.devices] == [device.id]
    assert complete.devices[0].device_name == "Living-room TV"
    assert complete.preferences.theme == "light"
    assert complete.subscription.subscription_tier == "free"


async def test_get_complete_profile_with_no_devices_returns_an_empty_list(
    service: UserService,
):
    user_id = uuid4()
    await service.create_user_profile(user_id)

    complete = await service.get_complete_profile(user_id)

    assert complete.devices == []


async def test_get_complete_profile_omits_deactivated_devices(service: UserService):
    user_id = uuid4()
    await service.create_user_profile(user_id)
    device = await service.register_device(
        user_id, _device_request("dev-2", "Phone"), "203.0.113.10"
    )
    await service.deactivate_device(device.id, user_id)

    complete = await service.get_complete_profile(user_id)

    assert complete.devices == []


# ---------------------------------------------------------------------------
# update_device
# ---------------------------------------------------------------------------


async def test_update_device_applies_the_patch_and_commits(service: UserService):
    user_id = uuid4()
    await service.create_user_profile(user_id)
    device = await service.register_device(
        user_id, _device_request("dev-3", "Old name"), "203.0.113.11"
    )

    updated = await service.update_device(
        device.id, _device_update(device_name="New name"), user_id
    )

    assert updated.id == device.id
    assert updated.device_name == "New name"


async def test_update_device_returns_404_for_an_unknown_device(service: UserService):
    from fastapi import HTTPException

    user_id = uuid4()
    await service.create_user_profile(user_id)

    with pytest.raises(HTTPException) as excinfo:
        await service.update_device(uuid4(), _device_update(device_name="x"), user_id)

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Device not found"


# ---------------------------------------------------------------------------
# UserProfileUpdateRequest null-rejection guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("bio", None),
        ("language", None),
        ("public_profile", None),
        ("newsletter_subscribed", None),
        ("marketing_emails", None),
    ],
)
def test_profile_update_rejects_explicit_nulls_on_non_nullable_fields(field, value):
    with pytest.raises(ValidationError) as excinfo:
        UserProfileUpdateRequest(**{field: value})

    assert "Field cannot be null" in str(excinfo.value)


@pytest.mark.parametrize(
    "field", ["avatar_url", "phone_number", "country", "date_of_birth", "timezone"]
)
def test_profile_update_allows_nulls_on_nullable_fields(field):
    """Only the five listed fields are non-nullable; the rest may be cleared."""
    request = UserProfileUpdateRequest(**{field: None})

    assert getattr(request, field) is None


def test_profile_update_accepts_values_for_every_guarded_field():
    request = UserProfileUpdateRequest(
        bio="hi",
        language="en-GB",
        public_profile=True,
        newsletter_subscribed=False,
        marketing_emails=True,
    )

    assert request.model_dump(exclude_unset=True) == {
        "bio": "hi",
        "language": "en-GB",
        "public_profile": True,
        "newsletter_subscribed": False,
        "marketing_emails": True,
    }


def test_profile_update_rejects_over_length_values():
    with pytest.raises(ValidationError):
        UserProfileUpdateRequest(bio="x" * 501)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _device_request(device_id: str, device_name: str):
    from app.schemas import UserDeviceRegisterRequest

    return UserDeviceRegisterRequest(
        device_id=device_id,
        device_name=device_name,
        device_type="smart_tv",
    )


def _device_update(**kwargs):
    return UserDeviceUpdateRequest(**kwargs)


def _preference_update(**kwargs):
    from app.schemas import UserPreferenceUpdateRequest

    return UserPreferenceUpdateRequest(**kwargs)
