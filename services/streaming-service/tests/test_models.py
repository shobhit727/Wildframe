"""Behavior-focused model tests for streaming-service.

Tests playback session defaults via SQLAlchemy column metadata,
and defaults for accessibility, DRM, and maturity models.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import inspect

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.models import (
    PlaybackSession,
    PlaybackSessionStatus,
    DeliveryProtocol,
    VideoManifest,
    TranscodingJob,
    StreamingQualityProfile,
    CDNRegion,
    StreamingStatistics,
    DownloadSession,
    TranscodingStatus,
)
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


def test_video_manifest_defaults():
    inc = inspect(VideoManifest).c.include_subtitles
    assert inc.default.arg is True
    cc = inspect(VideoManifest).c.include_closed_captions
    assert cc.default.arg is True
    live_edge = inspect(VideoManifest).c.live_edge_seconds
    assert live_edge.default.arg == 6
    gen = inspect(VideoManifest).c.generated_at
    assert gen.default is not None


def test_transcoding_job_defaults_and_status_transition():
    status_col = inspect(TranscodingJob).c.status
    assert status_col.default.arg == TranscodingStatus.PENDING
    priority = inspect(TranscodingJob).c.priority
    assert priority.default.arg == 5
    job = TranscodingJob(
        episode_id=uuid4(),
        content_id=uuid4(),
        input_file_path="/tmp/src.mp4",
        target_resolutions=["1080p"],
        target_bitrates=[5000],
    )
    # default not set on plain instance; column default verified above
    assert job.status is None
    job.status = TranscodingStatus.PROCESSING
    assert job.status == TranscodingStatus.PROCESSING


def test_streaming_quality_profile_defaults():
    fps = inspect(StreamingQualityProfile).c.fps
    assert fps.default.arg == 24
    video_codec = inspect(StreamingQualityProfile).c.video_codec
    assert video_codec.default.arg == "h264"
    audio_codec = inspect(StreamingQualityProfile).c.audio_codec
    assert audio_codec.default.arg == "aac"
    is_active = inspect(StreamingQualityProfile).c.is_active
    assert is_active.default.arg is True


def test_cdn_region_defaults():
    edge_ips = inspect(CDNRegion).c.edge_server_ips
    assert edge_ips.default.arg == []
    max_conc = inspect(CDNRegion).c.max_concurrent_streams
    assert max_conc.default.arg == 10000


def test_streaming_statistics_defaults():
    period_type = inspect(StreamingStatistics).c.period_type
    assert period_type.default is None
    total_streams = inspect(StreamingStatistics).c.total_streams
    assert total_streams.default.arg == 0
    avg_res = inspect(StreamingStatistics).c.average_resolution
    assert avg_res.default is None


def test_download_session_defaults_and_ttl():
    ttl = inspect(DownloadSession).c.download_ttl_days
    assert ttl.default.arg == 30
