"""Behavior-focused model tests for streaming-service.

Tests playback session defaults via SQLAlchemy column metadata,
and defaults for accessibility, DRM, and maturity models.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import inspect

from app.models import PlaybackSession, PlaybackSessionStatus, DeliveryProtocol
from app.models.accessibility import AccessibilityConfig
from app.models.drm import DRMConfig
from app.models.maturity import ContentMaturity


def test_playback_session_defaults_and_expiry():
    # column defaults
    status_col = inspect(PlaybackSession).c.status
    assert status_col.default.arg == PlaybackSessionStatus.ACTIVE
    protocol_col = inspect(PlaybackSession).c.protocol
    assert protocol_col.default.arg == DeliveryProtocol.HLS
    # expiry logic (2h ttl)
    start = datetime.now(UTC).replace(tzinfo=None)
    session = PlaybackSession(
        user_id=uuid4(),
        content_id=uuid4(),
        device_id="dev123",
        total_duration_seconds=3600,
        resolution="1080p",
        bitrate_kbps=5000,
        started_at=start,
    )
    expected = start + timedelta(hours=2)
    assert abs((session.expires_at - expected).total_seconds()) < 1


def test_accessibility_config_defaults():
    caps = inspect(AccessibilityConfig).c.captions_enabled
    assert caps.default.arg is True
    ad = inspect(AccessibilityConfig).c.audio_description
    assert ad.default.arg is False
    kb = inspect(AccessibilityConfig).c.keyboard_nav
    assert kb.default.arg is True


def test_drm_config_defaults():
    fp = inspect(DRMConfig).c.fairplay_enabled
    assert fp.default.arg is True
    wv = inspect(DRMConfig).c.widevine_enabled
    assert wv.default.arg is True
    device_limit = inspect(DRMConfig).c.device_limit
    assert device_limit.default.arg == 3
    expiry_hours = inspect(DRMConfig).c.expiry_hours
    assert expiry_hours.default.arg == 48
    offline = inspect(DRMConfig).c.offline_allowed
    assert offline.default.arg is False


def test_maturity_model_defaults():
    pg = inspect(ContentMaturity).c.requires_parental_consent
    assert pg.default.arg is False
    pr = inspect(ContentMaturity).c.purchase_restricted
    assert pr.default.arg is False
    spend_limit = inspect(ContentMaturity).c.spending_limit_cents
    assert spend_limit.default is None
    screen_time = inspect(ContentMaturity).c.screen_time_limit_minutes
    assert screen_time.default is None
    bedtime_start = inspect(ContentMaturity).c.bedtime_start
    assert bedtime_start.default is None
    bedtime_end = inspect(ContentMaturity).c.bedtime_end
    assert bedtime_end.default is None
