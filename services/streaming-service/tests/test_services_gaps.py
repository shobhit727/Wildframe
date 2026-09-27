# ruff: noqa: F811 -- test parameters intentionally share the imported fixture names

"""Service-layer coverage for StreamingService.

Everything runs against a real PostgreSQL session (conftest.db_session) so the
concurrency guards, the terminal-state rules and the rollback contract are
exercised as they behave in production rather than through mocked repositories.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from _pg_fixtures import (  # noqa: F401  - registers the shared PG fixtures
    broken_transaction,
    db_session,
    engine,
    event_loop,
    postgres_url,
    schema,
)
from app.models import PlaybackSession, PlaybackSessionStatus
from app.repositories import PlaybackSessionRepository, QualityProfileRepository
from app.schemas import (
    DownloadSessionCreateRequest,
    ManifestGenerationRequest,
    PlaybackSessionCreateRequest,
    PlaybackSessionUpdateRequest,
    QualityProfileCreateRequest,
    TranscodingJobCreateRequest,
)
from app.services import StreamingService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(db_session: AsyncSession) -> StreamingService:
    return StreamingService(db_session)


async def start_session(
    service: StreamingService,
    *,
    user_id=None,
    episode_id=None,
    content_id=None,
    device_id: str = "dev-1",
) -> PlaybackSession:
    """Start a real ACTIVE playback session through the service."""
    return await service.start_playback_session(
        PlaybackSessionCreateRequest(
            user_id=user_id or uuid4(),
            content_id=content_id or uuid4(),
            episode_id=episode_id,
            device_id=device_id,
        )
    )


async def assert_transaction_is_usable(session: AsyncSession) -> None:
    """A rolled-back session can immediately start a fresh transaction.

    This is what proves the ``except Exception -> rollback`` branch really ran
    rather than leaving the session poisoned.
    """
    result = await session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def count_playback_sessions(session: AsyncSession) -> int:
    from sqlalchemy import func, select

    result = await session.execute(select(func.count()).select_from(PlaybackSession))
    return result.scalar_one()


# --- update_playback_session ------------------------------------------------


async def test_update_playback_session_returns_none_for_an_unknown_id(
    service: StreamingService,
):
    result = await service.update_playback_session(
        uuid4(), PlaybackSessionUpdateRequest(current_position_seconds=30)
    )

    assert result is None


async def test_update_playback_session_persists_the_new_fields(
    service: StreamingService, db_session: AsyncSession
):
    session = await start_session(service, device_id="dev-happy")

    updated = await service.update_playback_session(
        session.id,
        PlaybackSessionUpdateRequest(
            current_position_seconds=120, resolution="720p", dropped_frames=4
        ),
    )
    assert updated is not None
    assert updated.current_position_seconds == 120
    assert updated.resolution == "720p"
    assert updated.dropped_frames == 4

    # The service commits, so the change outlives a rollback of our own session.
    await db_session.rollback()
    reloaded = await service.get_playback_session(session.id)
    assert reloaded is not None
    assert reloaded.current_position_seconds == 120
    assert reloaded.resolution == "720p"


async def test_update_playback_session_stamps_ended_at_when_marked_terminal(
    service: StreamingService,
):
    session = await start_session(service)
    before = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

    updated = await service.update_playback_session(
        session.id, PlaybackSessionUpdateRequest(status="completed")
    )

    assert updated is not None
    assert updated.status == PlaybackSessionStatus.COMPLETED
    assert updated.ended_at is not None
    assert updated.ended_at >= before


async def test_update_playback_session_rejects_reopening_an_ended_session(
    service: StreamingService,
):
    session = await start_session(service)
    await service.update_playback_session(
        session.id, PlaybackSessionUpdateRequest(status="completed")
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.update_playback_session(
            session.id, PlaybackSessionUpdateRequest(status="active")
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "Session has ended"


async def test_update_playback_session_treats_a_repeated_terminal_status_as_a_no_op(
    service: StreamingService,
):
    """Re-sending the same terminal status is idempotent, not a conflict."""
    session = await start_session(service)
    await service.update_playback_session(
        session.id,
        PlaybackSessionUpdateRequest(status="completed", current_position_seconds=999),
    )

    result = await service.update_playback_session(
        session.id,
        PlaybackSessionUpdateRequest(status="completed", current_position_seconds=5),
    )

    assert result is not None
    assert result.status == PlaybackSessionStatus.COMPLETED
    # A terminal session is frozen: the late position update is discarded.
    assert result.current_position_seconds == 999


async def test_update_playback_session_refuses_to_reactivate_beyond_the_concurrency_cap(
    service: StreamingService, db_session: AsyncSession
):
    user = uuid4()
    repo = PlaybackSessionRepository(db_session)
    # Seeded through the repository: start_playback_session applies
    # newest-device-wins and would never leave more than MAX_ACTIVE_SESSIONS
    # active, so the at-capacity state has to be built directly.
    seeded = [
        await repo.create(
            user_id=user,
            content_id=uuid4(),
            episode_id=None,
            device_id=f"dev-{index}",
            protocol="hls",
            resolution="720p",
            bitrate_kbps=2500,
            total_duration_seconds=600,
        )
        for index in range(settings.MAX_ACTIVE_SESSIONS + 1)
    ]
    await repo.commit()
    target_id = seeded[-1].id
    await repo.update(target_id, status=PlaybackSessionStatus.PAUSED)
    await repo.commit()
    assert len(await service.get_active_sessions(user)) == settings.MAX_ACTIVE_SESSIONS

    with pytest.raises(HTTPException) as excinfo:
        await service.update_playback_session(
            target_id, PlaybackSessionUpdateRequest(status="active")
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "Maximum concurrent sessions reached"
    # The refused transition must not have mutated the paused session.
    reloaded = await service.get_playback_session(target_id)
    assert reloaded is not None
    assert reloaded.status == PlaybackSessionStatus.PAUSED


# --- check_session_valid_for_playback / require_manifest_session ------------


async def test_playback_check_rejects_a_different_episode(service: StreamingService):
    episode = uuid4()
    session = await start_session(service, episode_id=episode)

    assert await service.check_session_valid_for_playback(session.id, session.user_id, episode)
    assert not await service.check_session_valid_for_playback(
        session.id, session.user_id, uuid4()
    )


async def test_require_manifest_session_grants_access_to_the_session_owner(
    service: StreamingService,
):
    episode, content = uuid4(), uuid4()
    session = await start_session(service, episode_id=episode, content_id=content)

    assert await service.require_manifest_session(session.user_id, episode, content) is None


async def test_require_manifest_session_rejects_a_mismatched_content_id(
    service: StreamingService,
):
    """The episode matches but the content does not, so access is refused."""
    episode = uuid4()
    session = await start_session(service, episode_id=episode, content_id=uuid4())

    with pytest.raises(HTTPException) as excinfo:
        await service.require_manifest_session(session.user_id, episode, uuid4())

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "An active session for this asset is required"


async def test_require_manifest_session_rejects_a_non_active_session(
    service: StreamingService, db_session: AsyncSession
):
    """A matching episode/content pair still fails once the session is paused."""
    episode, content = uuid4(), uuid4()
    session = await start_session(service, episode_id=episode, content_id=content)
    await PlaybackSessionRepository(db_session).update(
        session.id, status=PlaybackSessionStatus.PAUSED
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.require_manifest_session(session.user_id, episode, content)

    assert excinfo.value.status_code == 403


# --- rollback contract on write failures ------------------------------------


async def test_end_playback_session_rolls_back_and_reraises_on_a_db_failure(
    service: StreamingService, broken_transaction: AsyncSession
):
    with pytest.raises(DBAPIError):
        await service.end_playback_session(uuid4())

    # The rollback in the except branch is what makes this query possible again.
    await assert_transaction_is_usable(broken_transaction)


async def test_generate_manifest_rolls_back_and_reraises_on_a_db_failure(
    service: StreamingService, broken_transaction: AsyncSession
):
    with pytest.raises(DBAPIError):
        await service.generate_manifest(
            ManifestGenerationRequest(episode_id=uuid4(), content_id=uuid4())
        )

    await assert_transaction_is_usable(broken_transaction)


async def test_create_transcoding_job_rolls_back_and_reraises_on_a_db_failure(
    service: StreamingService, broken_transaction: AsyncSession
):
    with pytest.raises(DBAPIError):
        await service.create_transcoding_job(
            TranscodingJobCreateRequest(
                episode_id=uuid4(), content_id=uuid4(), input_file_path="/in.mp4"
            )
        )

    await assert_transaction_is_usable(broken_transaction)


async def test_create_download_session_rolls_back_and_reraises_on_a_db_failure(
    service: StreamingService, broken_transaction: AsyncSession
):
    with pytest.raises(DBAPIError):
        await service.create_download_session(
            DownloadSessionCreateRequest(user_id=uuid4(), episode_id=uuid4(), device_id="dev-1")
        )

    await assert_transaction_is_usable(broken_transaction)


async def test_create_quality_profile_rolls_back_and_reraises_on_a_duplicate_name(
    service: StreamingService, db_session: AsyncSession
):
    """``name`` is UNIQUE, so a second profile with the same name is a real error."""
    request = QualityProfileCreateRequest(
        name="dupe",
        resolution="720p",
        bitrate_kbps=2500,
        min_bandwidth_kbps=1000,
        max_bandwidth_kbps=4000,
    )
    await QualityProfileRepository(db_session).create(
        name=request.name,
        resolution=request.resolution,
        bitrate_kbps=request.bitrate_kbps,
        min_bandwidth_kbps=request.min_bandwidth_kbps,
        max_bandwidth_kbps=request.max_bandwidth_kbps,
    )
    await db_session.commit()
    assert await count_playback_sessions(db_session) == 0

    with pytest.raises(DBAPIError):
        await service.create_quality_profile(request)

    # The committed row is untouched and the session is usable again.
    await assert_transaction_is_usable(db_session)
    assert await QualityProfileRepository(db_session).get_by_name("dupe") is not None


async def test_update_transcoding_progress_rejects_a_progress_value_the_db_cannot_store(
    service: StreamingService,
):
    """progress_percent is an INTEGER column, so an oversized value is a real error."""
    job = await service.create_transcoding_job(
        TranscodingJobCreateRequest(
            episode_id=uuid4(), content_id=uuid4(), input_file_path="/in.mp4"
        )
    )
    job_id = job.id

    with pytest.raises(DBAPIError):
        await service.update_transcoding_progress(job_id, 2**40)

    await assert_transaction_is_usable(service.session)


async def test_complete_transcoding_job_rejects_a_non_serialisable_output_map(
    service: StreamingService,
):
    """output_paths lands in a JSONB column, so a non-JSON value cannot be stored."""
    job = await service.create_transcoding_job(
        TranscodingJobCreateRequest(
            episode_id=uuid4(), content_id=uuid4(), input_file_path="/in.mp4"
        )
    )
    job_id = job.id

    with pytest.raises(StatementError):
        await service.complete_transcoding_job(job_id, {"1080p": object()})

    await assert_transaction_is_usable(service.session)
    reloaded = await service.get_transcoding_job(job_id)
    assert reloaded is not None
    assert reloaded.status.name == "PENDING"


# --- download session reads -------------------------------------------------


async def test_get_download_session_round_trips_and_returns_none_when_missing(
    service: StreamingService,
):
    user, episode = uuid4(), uuid4()
    created = await service.create_download_session(
        DownloadSessionCreateRequest(user_id=user, episode_id=episode, device_id="dev-1")
    )

    found = await service.get_download_session(created.id)

    assert found is not None
    assert found.user_id == user
    assert found.episode_id == episode
    assert found.status == "queued"
    assert await service.get_download_session(uuid4()) is None


async def test_update_download_progress_rolls_back_on_an_unstorable_byte_count(
    service: StreamingService,
):
    """bytes_downloaded is an INTEGER column, so 2**40 overflows it."""
    created = await service.create_download_session(
        DownloadSessionCreateRequest(user_id=uuid4(), episode_id=uuid4(), device_id="dev-1")
    )
    await service.download_repo.update(created.id, total_bytes=1000)

    with pytest.raises(DBAPIError):
        await service.update_download_progress(created.id, 2**40)

    await assert_transaction_is_usable(service.session)
