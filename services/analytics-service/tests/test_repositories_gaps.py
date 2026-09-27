"""Repository-layer coverage for the analytics data-access classes.

Real SQLite sessions (matching the convention in test_repositories.py) so the
filters, ordering and partial-update semantics are actually exercised rather
than asserted against a stub.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.repositories import (
    ContentPerformanceMetricsRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
)


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def make_view_event(repo, content_id, viewer_id, created_at: datetime):
    event = await repo.create(
        content_id=content_id,
        viewer_id=viewer_id,
        watch_duration_seconds=10,
        content_duration_seconds=100,
        completion_pct=10.0,
    )
    event.created_at = created_at
    await repo.session.flush()
    return event


# --- ContentViewEventRepository ---------------------------------------------


@pytest.mark.asyncio
async def test_view_event_create_persists_every_supplied_field(async_session: AsyncSession):
    repo = ContentViewEventRepository(async_session)
    content, viewer = uuid4(), uuid4()
    started = datetime(2026, 2, 3, 4, 5, 6)
    completed = datetime(2026, 2, 3, 4, 9, 6)

    event = await repo.create(
        content_id=content,
        viewer_id=viewer,
        watch_duration_seconds=240,
        content_duration_seconds=600,
        completion_pct=40.0,
        playback_quality="1080p",
        started_at=started,
        completed_at=completed,
    )
    await async_session.commit()
    await async_session.refresh(event)

    assert event.id is not None
    assert event.content_id == content
    assert event.viewer_id == viewer
    assert event.watch_duration_seconds == 240
    assert event.content_duration_seconds == 600
    assert event.completion_pct == 40.0
    assert event.playback_quality == "1080p"
    assert event.started_at == started
    assert event.completed_at == completed


@pytest.mark.asyncio
async def test_view_event_create_leaves_optional_fields_unset(async_session: AsyncSession):
    repo = ContentViewEventRepository(async_session)

    event = await repo.create(content_id=uuid4(), viewer_id=uuid4())
    await async_session.commit()
    await async_session.refresh(event)

    assert event.playback_quality is None
    assert event.started_at is None
    assert event.completed_at is None
    assert event.watch_duration_seconds == 0
    assert event.completion_pct == 0.0


@pytest.mark.asyncio
async def test_get_by_content_is_scoped_ordered_newest_first_and_limited(
    async_session: AsyncSession,
):
    repo = ContentViewEventRepository(async_session)
    content, other = uuid4(), uuid4()

    oldest = await make_view_event(repo, content, uuid4(), datetime(2026, 1, 1, 0, 0, 0))
    newest = await make_view_event(repo, content, uuid4(), datetime(2026, 3, 1, 0, 0, 0))
    middle = await make_view_event(repo, content, uuid4(), datetime(2026, 2, 1, 0, 0, 0))
    await make_view_event(repo, other, uuid4(), datetime(2026, 4, 1, 0, 0, 0))
    await async_session.commit()

    everything = await repo.get_by_content(content)

    assert [e.id for e in everything] == [newest.id, middle.id, oldest.id]
    assert all(e.content_id == content for e in everything)
    assert [e.id for e in await repo.get_by_content(content, limit=2)] == [
        newest.id,
        middle.id,
    ]
    assert await repo.get_by_content(uuid4()) == []


@pytest.mark.asyncio
async def test_get_by_viewer_is_scoped_to_the_viewer(async_session: AsyncSession):
    repo = ContentViewEventRepository(async_session)
    viewer, other_viewer = uuid4(), uuid4()

    first = await make_view_event(repo, uuid4(), viewer, datetime(2026, 1, 1, 0, 0, 0))
    second = await make_view_event(repo, uuid4(), viewer, datetime(2026, 5, 1, 0, 0, 0))
    await make_view_event(repo, uuid4(), other_viewer, datetime(2026, 6, 1, 0, 0, 0))
    await async_session.commit()

    events = await repo.get_by_viewer(viewer)

    assert [e.id for e in events] == [second.id, first.id]
    assert all(e.viewer_id == viewer for e in events)
    assert [e.id for e in await repo.get_by_viewer(viewer, limit=1)] == [second.id]
    assert await repo.get_by_viewer(uuid4()) == []


# --- CreatorAnalyticsSnapshotRepository -------------------------------------


async def make_snapshot(repo, creator_id, period_start: datetime, period_end: datetime, **kwargs):
    return await repo.create(
        creator_id=creator_id,
        period_start=period_start,
        period_end=period_end,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_snapshot_create_persists_counters_and_period(async_session: AsyncSession):
    repo = CreatorAnalyticsSnapshotRepository(async_session)
    creator = uuid4()
    start, end = datetime(2026, 1, 1), datetime(2026, 1, 31)

    snapshot = await make_snapshot(
        repo,
        creator,
        start,
        end,
        total_views=1234,
        total_watch_hours=56.5,
        avg_completion_rate=72.5,
        unique_viewers=321,
        revenue_earned=999.99,
    )
    await async_session.commit()
    await async_session.refresh(snapshot)

    assert snapshot.id is not None
    assert snapshot.creator_id == creator
    assert snapshot.total_views == 1234
    assert snapshot.total_watch_hours == 56.5
    assert snapshot.avg_completion_rate == 72.5
    assert snapshot.unique_viewers == 321
    assert snapshot.revenue_earned == 999.99
    assert snapshot.period_start == start
    assert snapshot.period_end == end


@pytest.mark.asyncio
async def test_snapshot_create_defaults_every_counter_to_zero(async_session: AsyncSession):
    repo = CreatorAnalyticsSnapshotRepository(async_session)

    snapshot = await make_snapshot(repo, uuid4(), datetime(2026, 1, 1), datetime(2026, 1, 2))
    await async_session.commit()
    await async_session.refresh(snapshot)

    assert snapshot.total_views == 0
    assert snapshot.total_watch_hours == 0.0
    assert snapshot.avg_completion_rate == 0.0
    assert snapshot.unique_viewers == 0
    assert snapshot.revenue_earned == 0.0


@pytest.mark.asyncio
async def test_get_latest_for_creator_picks_the_greatest_period_end(
    async_session: AsyncSession,
):
    repo = CreatorAnalyticsSnapshotRepository(async_session)
    creator = uuid4()

    await make_snapshot(repo, creator, datetime(2026, 1, 1), datetime(2026, 1, 31))
    await make_snapshot(repo, creator, datetime(2026, 2, 1), datetime(2026, 2, 28))
    # A March window ends after the February one even though it starts later too.
    latest = await make_snapshot(repo, creator, datetime(2026, 3, 1), datetime(2026, 3, 10))
    # A later window for a different creator must not win.
    await make_snapshot(repo, uuid4(), datetime(2026, 6, 1), datetime(2026, 6, 30))
    await async_session.commit()

    found = await repo.get_latest_for_creator(creator)

    assert found is not None
    assert found.id == latest.id
    assert found.period_end == datetime(2026, 3, 10)
    assert await repo.get_latest_for_creator(uuid4()) is None


@pytest.mark.asyncio
async def test_get_for_creator_in_range_filters_both_bounds_and_orders_by_start(
    async_session: AsyncSession,
):
    repo = CreatorAnalyticsSnapshotRepository(async_session)
    creator = uuid4()
    window_start, window_end = datetime(2026, 2, 1), datetime(2026, 4, 30)

    early = await make_snapshot(repo, creator, datetime(2026, 2, 10), datetime(2026, 2, 20))
    late = await make_snapshot(repo, creator, datetime(2026, 4, 1), datetime(2026, 4, 15))
    # Starts before the window -> excluded.
    await make_snapshot(repo, creator, datetime(2026, 1, 1), datetime(2026, 1, 31))
    # Ends after the window -> excluded.
    await make_snapshot(repo, creator, datetime(2026, 3, 1), datetime(2026, 5, 31))
    # Fully inside the window but a different creator -> excluded.
    await make_snapshot(repo, uuid4(), datetime(2026, 2, 5), datetime(2026, 2, 25))
    await async_session.commit()

    in_range = await repo.get_for_creator_in_range(creator, window_start, window_end)

    assert [s.id for s in in_range] == [early.id, late.id]
    assert all(s.creator_id == creator for s in in_range)


@pytest.mark.asyncio
async def test_get_for_creator_in_range_is_empty_when_nothing_overlaps(
    async_session: AsyncSession,
):
    repo = CreatorAnalyticsSnapshotRepository(async_session)
    creator = uuid4()
    await make_snapshot(repo, creator, datetime(2020, 1, 1), datetime(2020, 1, 31))
    await async_session.commit()

    assert await repo.get_for_creator_in_range(
        creator, datetime(2026, 1, 1), datetime(2026, 12, 31)
    ) == []


# --- ContentPerformanceMetricsRepository ------------------------------------


@pytest.mark.asyncio
async def test_metrics_create_persists_every_window(async_session: AsyncSession):
    repo = ContentPerformanceMetricsRepository(async_session)
    content = uuid4()

    metrics = await repo.create(
        content_id=content,
        views_7d=10,
        views_30d=50,
        avg_completion_pct=33.3,
        revenue_7d=1.5,
        revenue_30d=7.5,
    )
    await async_session.commit()
    await async_session.refresh(metrics)

    assert metrics.id is not None
    assert metrics.content_id == content
    assert metrics.views_7d == 10
    assert metrics.views_30d == 50
    assert metrics.avg_completion_pct == 33.3
    assert metrics.revenue_7d == 1.5
    assert metrics.revenue_30d == 7.5
    assert metrics.updated_at is not None


@pytest.mark.asyncio
async def test_metrics_create_defaults_all_counters_to_zero(async_session: AsyncSession):
    repo = ContentPerformanceMetricsRepository(async_session)

    metrics = await repo.create(content_id=uuid4())
    await async_session.commit()
    await async_session.refresh(metrics)

    assert metrics.views_7d == 0
    assert metrics.views_30d == 0
    assert metrics.avg_completion_pct == 0.0
    assert metrics.revenue_7d == 0.0
    assert metrics.revenue_30d == 0.0


@pytest.mark.asyncio
async def test_metrics_get_by_content_round_trips_and_returns_none_when_missing(
    async_session: AsyncSession,
):
    repo = ContentPerformanceMetricsRepository(async_session)
    content = uuid4()
    await repo.create(content_id=content, views_7d=3)
    await async_session.commit()

    found = await repo.get_by_content(content)

    assert found is not None
    assert found.content_id == content
    assert found.views_7d == 3
    assert await repo.get_by_content(uuid4()) is None


@pytest.mark.asyncio
async def test_update_metrics_only_touches_the_supplied_fields(async_session: AsyncSession):
    repo = ContentPerformanceMetricsRepository(async_session)
    content = uuid4()
    await repo.create(
        content_id=content,
        views_7d=10,
        views_30d=20,
        avg_completion_pct=50.0,
        revenue_7d=1.0,
        revenue_30d=2.0,
    )
    await async_session.commit()

    updated = await repo.update_metrics(content, views_7d=99, revenue_30d=22.5)
    await async_session.commit()

    assert updated is not None
    assert updated.views_7d == 99
    assert updated.revenue_30d == 22.5
    # Untouched fields keep their previous values.
    assert updated.views_30d == 20
    assert updated.avg_completion_pct == 50.0
    assert updated.revenue_7d == 1.0


@pytest.mark.asyncio
async def test_update_metrics_with_no_arguments_is_a_no_op(async_session: AsyncSession):
    repo = ContentPerformanceMetricsRepository(async_session)
    content = uuid4()
    await repo.create(content_id=content, views_7d=10, avg_completion_pct=50.0)
    await async_session.commit()

    updated = await repo.update_metrics(content)
    await async_session.commit()

    assert updated is not None
    assert updated.views_7d == 10
    assert updated.avg_completion_pct == 50.0


@pytest.mark.asyncio
async def test_update_metrics_returns_none_when_there_is_no_row(async_session: AsyncSession):
    repo = ContentPerformanceMetricsRepository(async_session)

    assert await repo.update_metrics(uuid4(), views_7d=5) is None
