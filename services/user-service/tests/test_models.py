"""Behavioral model tests for user-service entities."""

from uuid import uuid4

from app.models import (
    UserProfile,
    UserDevice,
    UserPreference,
    UserSubscriptionProfile,
)


def test_user_profile_defaults() -> None:
    profile = UserProfile(user_id=uuid4())

    assert profile.is_active is True
    assert profile.public_profile is False
    assert profile.newsletter_subscribed is True
    assert profile.marketing_emails is False
    assert profile.completed_onboarding is False
    assert profile.profile_completeness == 0
    assert profile.language == "en-US"


def test_user_device_defaults_and_constraints() -> None:
    device = UserDevice(
        user_id=uuid4(),
        device_id="device-1",
        device_name="Test Device",
        device_type="web",
    )

    assert device.device_id == "device-1"
    assert device.device_name == "Test Device"
    assert device.device_type == "web"
    assert device.is_active is True
    assert device.is_trusted is False
    assert device.can_stream is True
    assert device.can_download is False


def test_user_preference_defaults() -> None:
    prefs = UserPreference(user_id=uuid4())

    assert prefs.theme == "dark"
    assert prefs.language == "en-US"
    assert prefs.subtitle_language == "en-US"
    assert prefs.subtitle_size == "medium"
    assert prefs.closed_captions is False
    assert prefs.autoplay is True
    assert prefs.autoplay_next_episode is True
    assert prefs.default_video_quality == "adaptive"
    assert prefs.default_audio_language == "en-US"
    assert prefs.content_rating == "PG-13"
    assert prefs.allow_explicit_content is True
    assert prefs.share_viewing_activity is False
    assert prefs.allow_recommendations is True
    assert prefs.data_collection is False
    assert prefs.email_new_content is True
    assert prefs.email_recommendations is False
    assert prefs.push_notifications is True


def test_user_subscription_profile_defaults() -> None:
    sub = UserSubscriptionProfile(user_id=uuid4())

    assert sub.subscription_tier == "free"
    assert sub.subscription_status == "active"
    assert sub.max_concurrent_streams == 1
    assert sub.can_download is False
    assert sub.can_use_4k is False
    assert sub.ad_free is False
