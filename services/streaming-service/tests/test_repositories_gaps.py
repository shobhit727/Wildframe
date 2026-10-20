# ruff: noqa: F811 -- test parameters intentionally share the imported fixture names

"""Repository-layer coverage for the streaming data-access classes.

These run against real PostgreSQL (see conftest.db_session) rather than mocks so
that the queries, column types and enum mappings are actually exercised.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlaybackSessionStatus, TranscodingStatus
from _pg_fixtures import (  # noqa: F401  - registers the shared PG fixtures
    broken_transaction,
    db_session,
    engine,
    event_loop,
    postgres_url,
    schema,
)
from app.repositories import (
    BaseRepository,
    CDNRegionRepository,
    DownloadSessionRepository,
    PlaybackSessionRepository,
    QualityProfileRepository,
    StreamingMetricsRepository,
    TranscodingJobRepository,
    VideoManifestRepository,
)


async def make_playback_session(
    repo: PlaybackSessionRepository,
    *,
    user_id=None,
    episode_id=None,
    status: PlaybackSessionStatus = PlaybackSessionStatus.ACTIVE,
) -> "object":
    """Create a playback session and force it to the given status."""
    session = await repo.create(
        user_id=user_id or uuid4(),
        content_id=uuid4(),
        episode_id=episode_id,
        device_id="dev-1",
        protocol="hls",
        resolution="1080p",
        bitrate_kbps=5000,
        total_duration_seconds=3600,
    )
    await repo.update(session.id, status=status)
    return session


# --- BaseRepository transaction helpers -------------------------------------


@pytest.mark.asyncio
async def test_base_rollback_discards_a_flushed_but_uncommitted_row(
    db_session: AsyncSession,
):
    """rollback() must undo a flushed INSERT, not just a not-yet-flushed one."""
    repo = PlaybackSessionRepository(db_session)
    session = await make_playback_session(repo)
    session_id = session.id

    await repo.rollback()

    assert await repo.get_by_id(session_id) is None


@pytest.mark.asyncio
async def test_base_repository_exposes_the_session_it_was_built_with(
    db_session: AsyncSession,
):
    base = BaseRepository(db_session)

    assert base.session is db_session


# --- PlaybackSessionRepository.get_active_sessions ---------------------------


@pytest.mark.asyncio
async def test_get_active_sessions_returns_only_active_rows_for_that_user(
    db_session: AsyncSession,
):
    repo = PlaybackSessionRepository(db_session)
    alice, bob = uuid4(), uuid4()

    first = await make_playback_session(repo, user_id=alice)
    second = await make_playback_session(repo, user_id=alice)
    await make_playback_session(repo, user_id=alice, status=PlaybackSessionStatus.COMPLETED)
    await make_playback_session(repo, user_id=bob)
    await repo.commit()

    active = {s.id for s in await repo.get_active_sessions(alice)}

    assert active == {first.id, second.id}
    assert all(s.user_id == alice for s in await repo.get_active_sessions(alice))


@pytest.mark.asyncio
async def test_get_active_sessions_is_empty_for_a_user_with_no_sessions(
    db_session: AsyncSession,
):
    repo = PlaybackSessionRepository(db_session)
    await make_playback_session(repo, status=PlaybackSessionStatus.COMPLETED)
    await repo.commit()

    assert list(await repo.get_active_sessions(uuid4())) == []


# --- PlaybackSessionRepository.get_oldest_active ----------------------------


@pytest.mark.asyncio
async def test_get_oldest_active_returns_the_least_recently_active_session(
    db_session: AsyncSession,
):
    repo = PlaybackSessionRepository(db_session)
    user = uuid4()

    newest = await make_playback_session(repo, user_id=user)
    oldest = await make_playback_session(repo, user_id=user)
    middle = await make_playback_session(repo, user_id=user)
    stamps = {
        newest.id: datetime(2026, 3, 3, 12, 0, 0),
        oldest.id: datetime(2026, 3, 1, 8, 0, 0),
        middle.id: datetime(2026, 3, 2, 9, 30, 0),
    }
    for session_id, stamp in stamps.items():
        await repo.update(session_id, last_activity_at=stamp)
    await repo.commit()

    found = await repo.get_oldest_active(user)

    assert found is not None
    assert found.id == oldest.id
    assert found.last_activity_at == datetime(2026, 3, 1, 8, 0, 0)


@pytest.mark.asyncio
async def test_get_oldest_active_breaks_activity_ties_on_the_lower_id(
    db_session: AsyncSession,
):
    """Ordering falls back to id.asc(), so equal stamps resolve deterministically."""
    repo = PlaybackSessionRepository(db_session)
    user = uuid4()

    a = await make_playback_session(repo, user_id=user)
    b = await make_playback_session(repo, user_id=user)
    tie = datetime(2026, 5, 5, 5, 5, 5)
    await repo.update(a.id, last_activity_at=tie)
    await repo.update(b.id, last_activity_at=tie)
    await repo.commit()

    found = await repo.get_oldest_active(user)

    assert found is not None
    assert found.id == min(a.id, b.id)


@pytest.mark.asyncio
async def test_get_oldest_active_ignores_finished_sessions(db_session: AsyncSession):
    repo = PlaybackSessionRepository(db_session)
    user = uuid4()

    stale = await make_playback_session(repo, user_id=user, status=PlaybackSessionStatus.COMPLETED)
    live = await make_playback_session(repo, user_id=user)
    await repo.update(stale.id, last_activity_at=datetime(2020, 1, 1, 0, 0, 0))
    await repo.update(live.id, last_activity_at=datetime(2026, 3, 3, 12, 0, 0))
    await repo.commit()

    found = await repo.get_oldest_active(user)

    assert found is not None
    assert found.id == live.id


@pytest.mark.asyncio
async def test_get_oldest_active_returns_none_when_nothing_is_active(
    db_session: AsyncSession,
):
    repo = PlaybackSessionRepository(db_session)
    user = uuid4()
    await make_playback_session(repo, user_id=user, status=PlaybackSessionStatus.PAUSED)
    await repo.commit()

    assert await repo.get_oldest_active(user) is None


# --- PlaybackSessionRepository.mark_completed guards ------------------------


@pytest.mark.asyncio
async def test_mark_completed_returns_none_for_an_unknown_session(
    db_session: AsyncSession,
):
    repo = PlaybackSessionRepository(db_session)

    assert await repo.mark_completed(uuid4()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal", [PlaybackSessionStatus.COMPLETED, PlaybackSessionStatus.INTERRUPTED]
)
async def test_mark_completed_does_not_rewrite_an_already_terminal_session(
    db_session: AsyncSession, terminal: PlaybackSessionStatus
):
    """A session that already ended keeps its terminal status and has no ended_at."""
    repo = PlaybackSessionRepository(db_session)
    session = await make_playback_session(repo, status=terminal)
    await repo.commit()
    assert session.ended_at is None

    result = await repo.mark_completed(session.id)
    await repo.commit()

    assert result is not None
    assert result.status == terminal
    assert result.ended_at is None


# --- VideoManifestRepository ------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_create_persists_variants_bitrates_and_content(
    db_session: AsyncSession,
):
    repo = VideoManifestRepository(db_session)
    episode, content = uuid4(), uuid4()

    manifest = await repo.create(
        episode_id=episode,
        content_id=content,
        protocol="hls",
        manifest_url="/manifests/x/hls.m3u8",
        manifest_content="#EXTM3U\n#EXT-X-ENDLIST\n",
        variants=["1080p", "720p"],
        available_bitrates=[5000, 2500],
    )
    await repo.commit()
    await db_session.refresh(manifest)

    assert manifest.id is not None
    assert manifest.episode_id == episode
    assert manifest.manifest_url == "/manifests/x/hls.m3u8"
    assert manifest.manifest_content.startswith("#EXTM3U")
    assert manifest.variants == ["1080p", "720p"]
    assert manifest.available_bitrates == [5000, 2500]


@pytest.mark.asyncio
async def test_manifest_get_by_id_round_trips_and_returns_none_when_missing(
    db_session: AsyncSession,
):
    repo = VideoManifestRepository(db_session)
    manifest = await repo.create(
        episode_id=uuid4(),
        content_id=uuid4(),
        protocol="dash",
        manifest_url="/m.mpd",
        manifest_content="<MPD/>",
        variants=["720p"],
        available_bitrates=[2500],
    )
    await repo.commit()

    found = await repo.get_by_id(manifest.id)

    assert found is not None
    assert found.manifest_url == "/m.mpd"
    assert await repo.get_by_id(uuid4()) is None


@pytest.mark.asyncio
async def test_manifest_lookup_by_episode_is_scoped_to_the_protocol(
    db_session: AsyncSession,
):
    repo = VideoManifestRepository(db_session)
    episode = uuid4()

    hls = await repo.create(
        episode_id=episode,
        content_id=uuid4(),
        protocol="hls",
        manifest_url="/hls.m3u8",
        manifest_content="#EXTM3U",
        variants=["720p"],
        available_bitrates=[2500],
    )
    await repo.create(
        episode_id=episode,
        content_id=uuid4(),
        protocol="dash",
        manifest_url="/dash.mpd",
        manifest_content="<MPD/>",
        variants=["1080p"],
        available_bitrates=[5000],
    )
    await repo.commit()

    assert (await repo.get_by_episode_and_protocol(episode, "hls")).id == hls.id
    dash = await repo.get_by_episode_and_protocol(episode, "dash")
    assert dash.manifest_url == "/dash.mpd"
    # A valid protocol that was never generated for this episode must miss.
    assert await repo.get_by_episode_and_protocol(episode, "smooth_streaming") is None
    assert await repo.get_by_episode_and_protocol(uuid4(), "hls") is None


# --- TranscodingJobRepository -----------------------------------------------


async def make_job(repo: TranscodingJobRepository, priority: int = 5):
    return await repo.create(
        episode_id=uuid4(),
        content_id=uuid4(),
        input_file_path="/media/in.mp4",
        target_resolutions=["1080p", "720p"],
        target_bitrates=[5000, 2500],
        priority=priority,
    )


@pytest.mark.asyncio
async def test_transcoding_create_persists_targets_and_defaults_to_pending(
    db_session: AsyncSession,
):
    repo = TranscodingJobRepository(db_session)

    job = await make_job(repo, priority=8)
    await repo.commit()
    await db_session.refresh(job)

    assert job.id is not None
    assert job.status == TranscodingStatus.PENDING
    assert job.priority == 8
    assert job.input_file_path == "/media/in.mp4"
    assert job.target_resolutions == ["1080p", "720p"]
    assert job.target_bitrates == [5000, 2500]


@pytest.mark.asyncio
async def test_get_pending_jobs_orders_by_priority_desc(db_session: AsyncSession):
    repo = TranscodingJobRepository(db_session)

    low = await make_job(repo, priority=2)
    high = await make_job(repo, priority=9)
    mid = await make_job(repo, priority=5)
    await repo.commit()

    pending = await repo.get_pending_jobs()

    assert [j.id for j in pending] == [high.id, mid.id, low.id]
    assert all(j.status == TranscodingStatus.PENDING for j in pending)


@pytest.mark.asyncio
async def test_get_pending_jobs_excludes_non_pending_jobs(db_session: AsyncSession):
    repo = TranscodingJobRepository(db_session)

    done = await make_job(repo, priority=1)
    await repo.update(done.id, status=TranscodingStatus.COMPLETED)
    waiting = await make_job(repo, priority=9)
    await repo.commit()

    assert [j.id for j in await repo.get_pending_jobs()] == [waiting.id]


@pytest.mark.asyncio
async def test_get_pending_jobs_honours_the_limit(db_session: AsyncSession):
    repo = TranscodingJobRepository(db_session)

    for priority in (1, 2, 3):
        await make_job(repo, priority=priority)
    await repo.commit()

    assert len(await repo.get_pending_jobs(limit=2)) == 2
    assert len(await repo.get_pending_jobs()) == 3


@pytest.mark.asyncio
async def test_transcoding_update_writes_the_supplied_columns(db_session: AsyncSession):
    repo = TranscodingJobRepository(db_session)
    job = await make_job(repo)
    await repo.commit()

    updated = await repo.update(
        job.id,
        progress_percent=42,
        error_message="decoder timeout",
        status=TranscodingStatus.FAILED,
    )
    await repo.commit()

    assert updated is not None
    assert updated.progress_percent == 42
    assert updated.error_message == "decoder timeout"
    assert updated.status == TranscodingStatus.FAILED


@pytest.mark.asyncio
async def test_transcoding_update_skips_none_and_unknown_attributes(
    db_session: AsyncSession,
):
    """``update`` guards on ``hasattr`` and ``value is not None``."""
    repo = TranscodingJobRepository(db_session)
    job = await make_job(repo, priority=7)
    await repo.commit()

    updated = await repo.update(job.id, priority=None, not_a_column="boom", progress_percent=5)
    await repo.commit()

    assert updated is not None
    assert updated.priority == 7
    assert updated.progress_percent == 5
    assert not hasattr(updated, "not_a_column")


@pytest.mark.asyncio
async def test_transcoding_update_returns_none_for_an_unknown_job(
    db_session: AsyncSession,
):
    repo = TranscodingJobRepository(db_session)

    assert await repo.update(uuid4(), progress_percent=10) is None


@pytest.mark.asyncio
async def test_transcoding_get_by_id_round_trips_and_returns_none_when_missing(
    db_session: AsyncSession,
):
    repo = TranscodingJobRepository(db_session)
    job = await make_job(repo, priority=3)
    await repo.commit()

    found = await repo.get_by_id(job.id)

    assert found is not None
    assert found.priority == 3
    assert found.target_bitrates == [5000, 2500]
    assert await repo.get_by_id(uuid4()) is None


# --- QualityProfileRepository -----------------------------------------------


@pytest.mark.asyncio
async def test_quality_profile_create_applies_extra_kwargs(db_session: AsyncSession):
    repo = QualityProfileRepository(db_session)

    profile = await repo.create(
        name="profile-a",
        resolution="720p",
        bitrate_kbps=2500,
        min_bandwidth_kbps=1500,
        max_bandwidth_kbps=4000,
        description="mid tier",
        fps=30,
        video_codec="h265",
        audio_codec="opus",
        supported_devices=["web", "android"],
    )
    await repo.commit()
    await db_session.refresh(profile)

    assert profile.id is not None
    assert profile.description == "mid tier"
    assert profile.fps == 30
    assert profile.video_codec == "h265"
    assert profile.audio_codec == "opus"
    assert profile.supported_devices == ["web", "android"]
    assert profile.min_bandwidth_kbps == 1500
    assert profile.max_bandwidth_kbps == 4000


@pytest.mark.asyncio
async def test_quality_profile_lookup_by_id_and_name(db_session: AsyncSession):
    repo = QualityProfileRepository(db_session)
    profile = await repo.create(
        name="profile-b",
        resolution="1080p",
        bitrate_kbps=5000,
        min_bandwidth_kbps=4000,
        max_bandwidth_kbps=9000,
    )
    await repo.commit()

    by_id = await repo.get_by_id(profile.id)
    by_name = await repo.get_by_name("profile-b")

    assert by_id is not None and by_id.name == "profile-b"
    assert by_name is not None and by_name.id == profile.id
    assert await repo.get_by_name("nope") is None
    assert await repo.get_by_id(uuid4()) is None


@pytest.mark.asyncio
async def test_get_all_active_profiles_excludes_inactive_ones(db_session: AsyncSession):
    repo = QualityProfileRepository(db_session)

    for name in ("active-1", "active-2"):
        await repo.create(
            name=name,
            resolution="720p",
            bitrate_kbps=2500,
            min_bandwidth_kbps=1000,
            max_bandwidth_kbps=4000,
        )
    await repo.create(
        name="retired",
        resolution="480p",
        bitrate_kbps=1000,
        min_bandwidth_kbps=0,
        max_bandwidth_kbps=1500,
        is_active=False,
    )
    await repo.commit()

    assert {p.name for p in await repo.get_all_active()} == {"active-1", "active-2"}


# --- CDNRegionRepository ----------------------------------------------------


@pytest.mark.asyncio
async def test_cdn_region_create_applies_extra_kwargs(db_session: AsyncSession):
    repo = CDNRegionRepository(db_session)

    region = await repo.create(
        region_code="us-east",
        region_name="US East",
        country="US",
        cdn_provider="cloudflare",
        bandwidth_capacity_gbps=400.0,
        latitude=40.7,
        longitude=-74.0,
        max_concurrent_streams=5000,
        edge_server_ips=["10.0.0.1", "10.0.0.2"],
    )
    await repo.commit()
    await db_session.refresh(region)

    assert region.id is not None
    assert region.latitude == 40.7
    assert region.longitude == -74.0
    assert region.max_concurrent_streams == 5000
    assert region.edge_server_ips == ["10.0.0.1", "10.0.0.2"]
    assert region.bandwidth_capacity_gbps == 400.0


@pytest.mark.asyncio
async def test_cdn_region_lookup_by_id_and_active_listing(db_session: AsyncSession):
    repo = CDNRegionRepository(db_session)

    live = await repo.create(
        region_code="eu-west",
        region_name="EU West",
        country="IE",
        cdn_provider="akamai",
        bandwidth_capacity_gbps=200.0,
    )
    await repo.create(
        region_code="ap-south",
        region_name="AP South",
        country="IN",
        cdn_provider="akamai",
        bandwidth_capacity_gbps=100.0,
        is_active=False,
    )
    await repo.commit()

    assert [r.id for r in await repo.get_all_active()] == [live.id]
    found = await repo.get_by_id(live.id)
    assert found is not None and found.cdn_provider == "akamai"
    assert await repo.get_by_id(uuid4()) is None


# --- StreamingMetricsRepository --------------------------------------------


@pytest.mark.asyncio
async def test_metrics_create_aggregates_the_sample_into_an_hourly_bucket(
    db_session: AsyncSession,
):
    repo = StreamingMetricsRepository(db_session)
    content = uuid4()

    stats = await repo.create(
        content_id=content,
        bandwidth_mbps=25.0,
        resolution="1080p",
        bitrate_kbps=5000,
        buffer_seconds=0.25,
        stalls=3,
    )
    await repo.commit()
    await db_session.refresh(stats)

    assert stats.id is not None
    assert stats.content_id == content
    assert stats.period_start.minute == 0
    assert stats.period_start.second == 0
    assert stats.period_start.microsecond == 0
    assert stats.period_type == "hourly"
    assert stats.total_streams == 1
    assert stats.average_bitrate_kbps == 5000
    assert stats.average_buffer_ratio == 0.25
    assert stats.stalls_per_session == 3.0
    assert stats.period_end >= stats.period_start


@pytest.mark.asyncio
async def test_metrics_get_by_id_round_trips_and_returns_none_when_missing(
    db_session: AsyncSession,
):
    repo = StreamingMetricsRepository(db_session)
    stats = await repo.create(
        content_id=uuid4(),
        bandwidth_mbps=5.0,
        resolution="480p",
        bitrate_kbps=1000,
        buffer_seconds=1.5,
        stalls=0,
    )
    await repo.commit()

    found = await repo.get_by_id(stats.id)

    assert found is not None
    assert found.average_bitrate_kbps == 1000
    assert found.stalls_per_session == 0.0
    assert await repo.get_by_id(uuid4()) is None


# --- DownloadSessionRepository ----------------------------------------------


@pytest.mark.asyncio
async def test_download_create_starts_queued_with_no_progress(db_session: AsyncSession):
    repo = DownloadSessionRepository(db_session)
    user, episode = uuid4(), uuid4()

    download = await repo.create(
        user_id=user, episode_id=episode, device_id="dev-9", resolution="720p", total_bytes=2048
    )
    await repo.commit()
    await db_session.refresh(download)

    assert download.id is not None
    assert download.user_id == user
    assert download.episode_id == episode
    assert download.status == "queued"
    assert download.total_bytes == 2048
    assert download.bytes_downloaded == 0
    assert download.progress_percent == 0
    assert download.download_ttl_days == 30


@pytest.mark.asyncio
async def test_get_user_downloads_is_scoped_to_the_user(db_session: AsyncSession):
    repo = DownloadSessionRepository(db_session)
    alice, bob = uuid4(), uuid4()

    for _ in range(2):
        await repo.create(
            user_id=alice,
            episode_id=uuid4(),
            device_id="dev-1",
            resolution="720p",
            total_bytes=100,
        )
    await repo.create(
        user_id=bob, episode_id=uuid4(), device_id="dev-2", resolution="480p", total_bytes=100
    )
    await repo.commit()

    mine = await repo.get_user_downloads(alice)

    assert len(mine) == 2
    assert all(d.user_id == alice for d in mine)
    assert await repo.get_user_downloads(uuid4()) == []


@pytest.mark.asyncio
async def test_download_update_writes_columns_and_skips_none(db_session: AsyncSession):
    repo = DownloadSessionRepository(db_session)
    download = await repo.create(
        user_id=uuid4(),
        episode_id=uuid4(),
        device_id="dev-1",
        resolution="720p",
        total_bytes=200,
    )
    await repo.commit()

    updated = await repo.update(
        download.id,
        status="downloading",
        bytes_downloaded=100,
        status_is_not_a_column=None,
        bogus=object(),
    )
    await repo.commit()

    assert updated is not None
    assert updated.status == "downloading"
    assert updated.bytes_downloaded == 100


@pytest.mark.asyncio
async def test_download_lookup_by_id_and_update_of_a_missing_row(db_session: AsyncSession):
    repo = DownloadSessionRepository(db_session)
    download = await repo.create(
        user_id=uuid4(),
        episode_id=uuid4(),
        device_id="dev-1",
        resolution="480p",
        total_bytes=50,
    )
    await repo.commit()

    found = await repo.get_by_id(download.id)
    assert found is not None and found.resolution == "480p"
    assert await repo.get_by_id(uuid4()) is None
    assert await repo.update(uuid4(), status="completed") is None
