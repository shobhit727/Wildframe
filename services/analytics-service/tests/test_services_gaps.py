"""Service-layer coverage for AnalyticsService against real repositories.

The existing test_service.py drives the validation rules through in-memory fakes.
These tests wire AnalyticsService to real repositories on a real SQLite session
so the service/repository seam and the recursive payload guards are exercised
end to end.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.repositories import (
    ContentPerformanceMetricsRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
    EventRepository,
)
from app.services import MAX_EVENT_DATA_DEPTH, AnalyticsService


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def service(async_session: AsyncSession) -> AnalyticsService:
    return AnalyticsService(
        event_repo=EventRepository(async_session),
        view_repo=ContentViewEventRepository(async_session),
        creator_repo=CreatorAnalyticsSnapshotRepository(async_session),
        content_repo=ContentPerformanceMetricsRepository(async_session),
    )


def nested_dict(depth: int) -> dict:
    """A dict chain whose innermost value is inspected at exactly ``depth``."""
    root: dict = {}
    node = root
    for _ in range(depth):
        node["n"] = {}
        node = node["n"]
    return root


def nested_list(depth: int) -> list:
    """A list chain that sits one level below ``depth`` once wrapped in a dict."""
    root: list = []
    node = root
    for _ in range(depth - 1):
        child: list = []
        node.append(child)
        node = child
    return root


# --- payload depth guard ----------------------------------------------------


@pytest.mark.asyncio
async def test_log_event_rejects_event_data_nested_past_the_depth_limit(
    service: AnalyticsService, async_session: AsyncSession
):
    user = uuid4()

    with pytest.raises(ValueError, match=f"nesting depth exceeds {MAX_EVENT_DATA_DEPTH}"):
        await service.log_event(user, "deep", nested_dict(MAX_EVENT_DATA_DEPTH + 1))

    assert await EventRepository(async_session).get_by_user(user) == []


@pytest.mark.asyncio
async def test_log_event_accepts_event_data_at_exactly_the_depth_limit(
    service: AnalyticsService, async_session: AsyncSession
):
    """The guard is ``depth > MAX``, so the limit itself is still accepted."""
    user = uuid4()

    await service.log_event(user, "at-limit", nested_dict(MAX_EVENT_DATA_DEPTH))
    await async_session.commit()

    stored = await EventRepository(async_session).get_by_user(user)
    assert [e.event_type for e in stored] == ["at-limit"]


@pytest.mark.asyncio
async def test_log_event_rejects_depth_reached_through_nested_lists(
    service: AnalyticsService, async_session: AsyncSession
):
    """The guard recurses into lists, not just dicts."""
    user = uuid4()

    with pytest.raises(ValueError, match=f"nesting depth exceeds {MAX_EVENT_DATA_DEPTH}"):
        await service.log_event(user, "deep-list", {"seq": nested_list(MAX_EVENT_DATA_DEPTH + 1)})

    assert await EventRepository(async_session).get_by_user(user) == []


@pytest.mark.asyncio
async def test_log_event_keeps_a_list_nested_within_the_depth_limit(
    service: AnalyticsService, async_session: AsyncSession
):
    """A list inside the limit is traversed and then stored intact."""
    user = uuid4()
    payload = {"seq": nested_list(MAX_EVENT_DATA_DEPTH)}

    await service.log_event(user, "list-ok", payload)
    await async_session.commit()

    stored = await EventRepository(async_session).get_by_user(user)
    assert len(stored) == 1
    assert stored[0].event_data == payload


# --- non-finite number guard ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
async def test_log_event_rejects_non_finite_numbers_inside_a_list(
    service: AnalyticsService, async_session: AsyncSession, bad: float
):
    user = uuid4()

    with pytest.raises(ValueError, match="Non-finite numbers"):
        await service.log_event(user, "metric", {"values": [1.0, bad, 3.0]})

    assert await EventRepository(async_session).get_by_user(user) == []


@pytest.mark.asyncio
async def test_log_event_keeps_a_list_of_finite_numbers(
    service: AnalyticsService, async_session: AsyncSession
):
    user = uuid4()

    await service.log_event(user, "metric", {"values": [1.0, 2.5, 3.0]})
    await async_session.commit()

    stored = await EventRepository(async_session).get_by_user(user)
    assert len(stored) == 1
    assert stored[0].event_data == {"values": [1.0, 2.5, 3.0]}


# --- missing-row lookups ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_creator_analytics_returns_none_when_the_creator_has_no_snapshot(
    service: AnalyticsService,
):
    empty = uuid4()
    # Another creator with data must not make the empty one resolve.
    await service.save_creator_snapshot(
        uuid4(),
        total_views=5,
        period_start=datetime(2026, 1, 1),
        period_end=datetime(2026, 1, 31),
    )

    assert await service.get_creator_analytics(empty) is None


@pytest.mark.asyncio
async def test_get_content_performance_returns_none_when_the_content_has_no_metrics(
    service: AnalyticsService,
):
    empty = uuid4()
    await service.update_content_performance(uuid4(), views_7d=1)

    assert await service.get_content_performance(empty) is None


# --- service/repository seam ------------------------------------------------


@pytest.mark.asyncio
async def test_get_creator_analytics_returns_the_most_recent_snapshot(
    service: AnalyticsService, async_session: AsyncSession
):
    creator = uuid4()
    await service.save_creator_snapshot(
        creator,
        total_views=10,
        total_watch_hours=1.5,
        avg_completion_rate=25.0,
        unique_viewers=4,
        revenue_earned=3.5,
        period_start=datetime(2026, 1, 1),
        period_end=datetime(2026, 1, 31),
    )
    await service.save_creator_snapshot(
        creator,
        total_views=99,
        total_watch_hours=9.5,
        avg_completion_rate=80.0,
        unique_viewers=40,
        revenue_earned=93.5,
        period_start=datetime(2026, 2, 1),
        period_end=datetime(2026, 2, 28),
    )
    await async_session.commit()

    result = await service.get_creator_analytics(creator)

    assert result == {
        "creator_id": str(creator),
        "total_views": 99,
        "total_watch_hours": 9.5,
        "avg_completion_rate": 80.0,
        "unique_viewers": 40,
        "revenue_earned": 93.5,
        "period_start": datetime(2026, 2, 1).isoformat(),
        "period_end": datetime(2026, 2, 28).isoformat(),
    }


@pytest.mark.asyncio
async def test_get_content_performance_returns_the_stored_metrics(
    service: AnalyticsService, async_session: AsyncSession
):
    content = uuid4()
    # update_content_performance is an update, not an upsert: the row must exist.
    await service.content_repo.create(content_id=content)
    await async_session.commit()
    await service.update_content_performance(
        content,
        views_7d=7,
        views_30d=70,
        avg_completion_pct=55.5,
        revenue_7d=1.25,
        revenue_30d=12.5,
    )
    await async_session.commit()

    result = await service.get_content_performance(content)

    assert result is not None
    assert result["content_id"] == str(content)
    assert result["views_7d"] == 7
    assert result["views_30d"] == 70
    assert result["avg_completion_pct"] == 55.5
    assert result["revenue_7d"] == 1.25
    assert result["revenue_30d"] == 12.5
    assert result["updated_at"] is not None


@pytest.mark.asyncio
async def test_update_content_performance_only_changes_the_supplied_fields(
    service: AnalyticsService, async_session: AsyncSession
):
    content = uuid4()
    await service.content_repo.create(content_id=content)
    await async_session.commit()
    await service.update_content_performance(
        content, views_7d=1, views_30d=2, avg_completion_pct=3.0, revenue_7d=4.0, revenue_30d=5.0
    )
    await async_session.commit()

    await service.update_content_performance(content, views_7d=100)
    await async_session.commit()

    result = await service.get_content_performance(content)
    assert result is not None
    assert result["views_7d"] == 100
    assert result["views_30d"] == 2
    assert result["avg_completion_pct"] == 3.0
    assert result["revenue_7d"] == 4.0
    assert result["revenue_30d"] == 5.0


@pytest.mark.asyncio
async def test_update_content_performance_returns_none_for_untracked_content(
    service: AnalyticsService,
):
    assert await service.update_content_performance(uuid4(), views_7d=1) is None


@pytest.mark.asyncio
async def test_record_view_event_persists_the_normalised_payload(
    service: AnalyticsService, async_session: AsyncSession
):
    """Quality is lower-cased and naive timestamps are treated as UTC before the write."""
    content, viewer = uuid4(), uuid4()

    event = await service.record_view_event(
        content_id=content,
        viewer_id=viewer,
        watch_duration_seconds=120,
        content_duration_seconds=600,
        completion_pct=20.0,
        playback_quality="  1080P ",
        started_at=datetime(2026, 1, 1, 10, 0, 0),
        completed_at=datetime(2026, 1, 1, 10, 2, 0),
    )
    await async_session.commit()

    assert event is not None
    assert event.playback_quality == "1080p"
    assert event.started_at == datetime(2026, 1, 1, 10, 0, 0, tzinfo=event.started_at.tzinfo)

    stored = await ContentViewEventRepository(async_session).get_by_content(content)
    assert [e.id for e in stored] == [event.id]
    assert stored[0].viewer_id == viewer
    assert stored[0].watch_duration_seconds == 120
    assert stored[0].completion_pct == 20.0


@pytest.mark.asyncio
async def test_save_creator_snapshot_persists_the_counters(
    service: AnalyticsService, async_session: AsyncSession
):
    creator = uuid4()

    snapshot = await service.save_creator_snapshot(
        creator,
        total_views=11,
        total_watch_hours=2.5,
        avg_completion_rate=42.0,
        unique_viewers=6,
        revenue_earned=7.25,
        period_start=datetime(2026, 3, 1),
        period_end=datetime(2026, 3, 31),
    )
    await async_session.commit()

    assert snapshot is not None
    stored = await CreatorAnalyticsSnapshotRepository(async_session).get_latest_for_creator(creator)
    assert stored is not None
    assert stored.total_views == 11
    assert stored.unique_viewers == 6
    assert stored.revenue_earned == 7.25
    assert stored.period_start == datetime(2026, 3, 1)


@pytest.mark.asyncio
async def test_save_creator_snapshot_without_a_period_raises_an_integrity_error(
    service: AnalyticsService,
):
    """PINS A BUG: the signature defaults the periods to None, the columns are NOT NULL.

    save_creator_snapshot() advertises period_start/period_end as optional, but
    CreatorAnalyticsSnapshot declares both as nullable=False with no default and
    the service does not validate them, so omitting them surfaces as a raw
    IntegrityError instead of a ValueError.
    """
    with pytest.raises(IntegrityError):
        await service.save_creator_snapshot(uuid4(), total_views=5)


@pytest.mark.asyncio
async def test_get_user_events_reads_back_what_log_event_wrote(
    service: AnalyticsService, async_session: AsyncSession
):
    user = uuid4()
    await service.log_event(user, "playback_started", {"position": 12})
    await service.log_event(user, "playback_paused", {"position": 44})
    await async_session.commit()

    events = await service.get_user_events(user)

    assert {e["event_type"] for e in events} == {"playback_started", "playback_paused"}
    by_type = {e["event_type"]: e["data"] for e in events}
    assert by_type["playback_started"] == {"position": 12}
    assert by_type["playback_paused"] == {"position": 44}
    assert all(e["timestamp"] for e in events)
