"""Behavioral model tests for user-service entities."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from uuid import uuid4

from app.models import (
    UserProfile,
    UserDevice,
    UserPreference,
    UserSubscriptionProfile,
    Base,
)


@pytest.fixture(scope="function")
def db_session():
    """Create an isolated in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()


def test_user_profile_defaults(db_session):
    profile = UserProfile(user_id=uuid4())
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    assert profile.is_active is True
    assert profile.public_profile is False
    assert profile.newsletter_subscribed is True
    assert profile.marketing_emails is False
    assert profile.completed_onboarding is False
    assert profile.profile_completeness == 0
    assert profile.language == "en-US"


def test_user_device_defaults_and_constraints(db_session):
    device = UserDevice(
        user_id=uuid4(),
        device_id="device-1",
        device_name="Test Device",
        device_type="web",
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(device)

    assert device.device_id == "device-1"
    assert device.device_name == "Test Device"
    assert device.device_type == "web"
    assert device.is_active is True
    assert device.is_trusted is False
    assert device.can_stream is True
    assert device.can_download is False


def test_user_preference_defaults(db_session):
    prefs = UserPreference(user_id=uuid4())
    db_session.add(prefs)
    db_session.commit()
    db_session.refresh(prefs)

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


def test_user_subscription_profile_defaults(db_session):
    sub = UserSubscriptionProfile(user_id=uuid4())
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

    assert sub.subscription_tier == "free"
    assert sub.subscription_status == "active"
    assert sub.max_concurrent_streams == 1
    assert sub.can_download is False
    assert sub.can_use_4k is False
    assert sub.ad_free is False