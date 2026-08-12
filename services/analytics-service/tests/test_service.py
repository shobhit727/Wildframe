"""Tests for Analytics Service business logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services import (
    MAX_EVENT_LIMIT,
    MAX_EVENT_TYPE_LENGTH,
    MAX_EVENT_DATA_BYTES,
    MAX_DURATION_SECONDS,
    AnalyticsService,
)


class FakeEventRepo:
    def __init__(self):
        self.events = []

    async def create(self, user_id, event_type, event_data=None, content_id=None):
        e = MagicMock()
        e.user_id = user_id
        e.event_type = event_type
        e.event_data = event_data
        e.content_id = content_id
        e.timestamp = datetime.now(UTC)
        self.events.append(e)
        return e

    async def get_by_user(self, user_id, limit=100):
        return [e for e in self.events if e.user_id == user_id][:limit]


class FakeViewRepo:
    def __init__(self):
        self.events = []

    async def create(self, **kwargs):
        e = MagicMock(**kwargs)
        self.events.append(e)
        return e

    async def get_by_content(self, content_id, limit=100):
        return [e for e in self.events if e.content_id == content_id][:limit]

    async def get_by_viewer(self, viewer_id, limit=100):
        return [e for e in self.events if e.viewer_id == viewer_id][:limit]


class FakeCreatorRepo:
    def __init__(self):
        self.snapshots = []

    async def create(self, **kwargs):
        s = MagicMock(**kwargs)
        self.snapshots.append(s)
        return s

    async def get_latest_for_creator(self, creator_id):
        matches = [s for s in self.snapshots if s.creator_id == creator_id]
        return max(matches, key=lambda s: s.period_end) if matches else None


class FakeContentRepo:
    def __init__(self):
        self.metrics = []

    async def get_by_content(self, content_id):
        for m in self.metrics:
            if m.content_id == content_id:
                return m
        return None

    async def create(self, **kwargs):
        m = MagicMock(**kwargs)
        self.metrics.append(m)
        return m

    async def update_metrics(self, content_id, **kwargs):
        m = await self.get_by_content(content_id)
        if m:
            for k, v in kwargs.items():
                if v is not None:
                    setattr(m, k, v)
        return m


class FakeDedupStore:
    """In-memory redis-like store supporting set(nx=True, ex=...)."""

    def __init__(self):
        self.data = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True


@pytest.fixture
def fake_repos():
    return {
        "event": FakeEventRepo(),
        "view": FakeViewRepo(),
        "creator": FakeCreatorRepo(),
        "content": FakeContentRepo(),
    }


@pytest.fixture
def dedup_store():
    return FakeDedupStore()


@pytest.fixture
def service(fake_repos, dedup_store):
    return AnalyticsService(
        event_repo=fake_repos["event"],
        view_repo=fake_repos["view"],
        creator_repo=fake_repos["creator"],
        content_repo=fake_repos["content"],
        dedup_store=dedup_store,
    )


class TestLogEventValidation:
    @pytest.mark.asyncio
    async def test_valid_event(self, service):
        uid = uuid4()
        await service.log_event(uid, "playback_started", {"pos": 10})
        assert len(service.event_repo.events) == 1

    @pytest.mark.asyncio
    async def test_rejects_empty_event_type(self, service):
        uid = uuid4()
        with pytest.raises(ValueError, match="event_type is required"):
            await service.log_event(uid, "  ")

    @pytest.mark.asyncio
    async def test_rejects_long_event_type(self, service):
        uid = uuid4()
        with pytest.raises(ValueError, match=f"event_type exceeds {MAX_EVENT_TYPE_LENGTH}"):
            await service.log_event(uid, "x" * (MAX_EVENT_TYPE_LENGTH + 1))

    @pytest.mark.asyncio
    async def test_rejects_nan_in_event_data(self, service):
        uid = uuid4()
        with pytest.raises(ValueError, match="Non-finite numbers"):
            await service.log_event(uid, "metric", {"value": float("nan")})

    @pytest.mark.asyncio
    async def test_rejects_inf_in_event_data(self, service):
        uid = uuid4()
        with pytest.raises(ValueError, match="Non-finite numbers"):
            await service.log_event(uid, "metric", {"value": float("inf")})

    @pytest.mark.asyncio
    async def test_rejects_nan_in_nested_event_data(self, service):
        uid = uuid4()
        with pytest.raises(ValueError, match="Non-finite numbers"):
            await service.log_event(uid, "metric", {"nested": {"value": float("nan")}})

    @pytest.mark.asyncio
    async def test_rejects_huge_event_data(self, service):
        uid = uuid4()
        big = {"blob": "x" * (MAX_EVENT_DATA_BYTES + 1)}
        with pytest.raises(ValueError, match=f"event_data exceeds {MAX_EVENT_DATA_BYTES}"):
            await service.log_event(uid, "metric", big)


class TestRecordViewEventValidation:
    @pytest.mark.asyncio
    async def test_valid_view_event(self, service):
        cid = uuid4()
        vid = uuid4()
        await service.record_view_event(
            content_id=cid,
            viewer_id=vid,
            watch_duration_seconds=120,
            content_duration_seconds=300,
            completion_pct=40.0,
            playback_quality="1080p",
        )
        assert len(service.view_repo.events) == 1

    @pytest.mark.asyncio
    async def test_rejects_negative_watch_duration(self, service):
        with pytest.raises(ValueError, match="Durations cannot be negative"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=-1,
                content_duration_seconds=300,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_content_duration(self, service):
        with pytest.raises(ValueError, match="Durations cannot be negative"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=-5,
            )

    @pytest.mark.asyncio
    async def test_rejects_excessive_duration(self, service):
        with pytest.raises(ValueError, match=f"Durations cannot exceed {MAX_DURATION_SECONDS}"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=MAX_DURATION_SECONDS + 1,
                content_duration_seconds=300,
            )

    @pytest.mark.asyncio
    async def test_rejects_nan_completion(self, service):
        with pytest.raises(ValueError, match="completion_pct must be a finite number"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=float("nan"),
            )

    @pytest.mark.asyncio
    async def test_rejects_inf_completion(self, service):
        with pytest.raises(ValueError, match="completion_pct must be a finite number"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=float("inf"),
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_completion(self, service):
        with pytest.raises(
            ValueError, match="completion_pct must be a finite number between 0 and 100"
        ):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=-1.0,
            )

    @pytest.mark.asyncio
    async def test_rejects_completion_above_100(self, service):
        with pytest.raises(
            ValueError, match="completion_pct must be a finite number between 0 and 100"
        ):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=150.0,
            )

    @pytest.mark.asyncio
    async def test_rejects_watch_above_content(self, service):
        with pytest.raises(
            ValueError, match="watch_duration_seconds cannot exceed content_duration_seconds"
        ):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=600,
                content_duration_seconds=300,
            )

    @pytest.mark.asyncio
    async def test_allows_watch_above_content_when_content_unknown(self, service):
        """When content_duration_seconds == 0 (unknown), any watch duration is accepted."""
        await service.record_view_event(
            content_id=uuid4(),
            viewer_id=uuid4(),
            watch_duration_seconds=600,
            content_duration_seconds=0,
            completion_pct=0.0,
        )
        assert len(service.view_repo.events) == 1

    @pytest.mark.asyncio
    async def test_normalizes_playback_quality(self, service):
        await service.record_view_event(
            content_id=uuid4(),
            viewer_id=uuid4(),
            watch_duration_seconds=10,
            content_duration_seconds=300,
            completion_pct=5.0,
            playback_quality="1080P",
        )
        # quality normalized to lowercase
        assert service.view_repo.events[0].playback_quality == "1080p"

    @pytest.mark.asyncio
    async def test_rejects_invalid_playback_quality(self, service):
        with pytest.raises(ValueError, match="Unsupported playback_quality"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=5.0,
                playback_quality="4320p",
            )

    @pytest.mark.asyncio
    async def test_rejects_future_started_at(self, service):
        future = datetime.now(UTC) + timedelta(minutes=10)
        with pytest.raises(ValueError, match="started_at cannot be in the future"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=5.0,
                started_at=future,
            )

    @pytest.mark.asyncio
    async def test_rejects_future_completed_at(self, service):
        future = datetime.now(UTC) + timedelta(minutes=10)
        with pytest.raises(ValueError, match="completed_at cannot be in the future"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=5.0,
                completed_at=future,
            )

    @pytest.mark.asyncio
    async def test_rejects_completed_before_started(self, service):
        started = datetime.now(UTC)
        completed = started - timedelta(minutes=5)
        with pytest.raises(ValueError, match="completed_at cannot precede started_at"):
            await service.record_view_event(
                content_id=uuid4(),
                viewer_id=uuid4(),
                watch_duration_seconds=10,
                content_duration_seconds=300,
                completion_pct=5.0,
                started_at=started,
                completed_at=completed,
            )

    @pytest.mark.asyncio
    async def test_normalizes_naive_timestamps_to_utc(self, service):
        """Naive datetimes are interpreted as UTC."""
        naive = datetime(2026, 1, 15, 12, 0, 0)  # no tzinfo
        await service.record_view_event(
            content_id=uuid4(),
            viewer_id=uuid4(),
            watch_duration_seconds=10,
            content_duration_seconds=300,
            completion_pct=5.0,
            started_at=naive,
            completed_at=naive,
        )
        assert service.view_repo.events[0].started_at.tzinfo is not None
        assert service.view_repo.events[0].completed_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_allows_valid_edge_cases(self, service):
        """Zero durations, 0% and 100% completion are valid."""
        await service.record_view_event(
            content_id=uuid4(),
            viewer_id=uuid4(),
            watch_duration_seconds=0,
            content_duration_seconds=0,
            completion_pct=0.0,
        )
        await service.record_view_event(
            content_id=uuid4(),
            viewer_id=uuid4(),
            watch_duration_seconds=300,
            content_duration_seconds=300,
            completion_pct=100.0,
        )
        assert len(service.view_repo.events) == 2


class TestGetUserEventsLimit:
    @pytest.mark.asyncio
    async def test_rejects_limit_zero(self, service):
        with pytest.raises(ValueError, match=f"limit must be between 1 and {MAX_EVENT_LIMIT}"):
            await service.get_user_events(uuid4(), limit=0)

    @pytest.mark.asyncio
    async def test_rejects_limit_above_max(self, service):
        with pytest.raises(ValueError, match=f"limit must be between 1 and {MAX_EVENT_LIMIT}"):
            await service.get_user_events(uuid4(), limit=MAX_EVENT_LIMIT + 1)

    @pytest.mark.asyncio
    async def test_accepts_valid_limit(self, service):
        uid = uuid4()
        await service.event_repo.create(uid, "e1")
        await service.event_repo.create(uid, "e2")
        await service.event_repo.create(uid, "e3")
        events = await service.get_user_events(uid, limit=2)
        assert len(events) == 2


class TestCreatorSnapshotValidation:
    @pytest.mark.asyncio
    async def test_valid_snapshot(self, service):
        cid = uuid4()
        await service.save_creator_snapshot(
            creator_id=cid,
            total_views=10,
            total_watch_hours=5.0,
            avg_completion_rate=50.0,
            unique_viewers=3,
            revenue_earned=2.5,
        )
        assert len(service.creator_repo.snapshots) == 1

    @pytest.mark.asyncio
    async def test_rejects_negative_counters(self, service):
        for label, bad_val in [
            ("total_views", -1),
            ("unique_viewers", -1),
            ("revenue_earned", -1.0),
            ("total_watch_hours", -1.0),
        ]:
            with pytest.raises(ValueError, match="cannot be negative"):
                await service.save_creator_snapshot(creator_id=uuid4(), **{label: bad_val})

    @pytest.mark.asyncio
    async def test_rejects_invalid_avg_completion(self, service):
        for bad_val in [-1.0, 101.0, float("nan"), float("inf")]:
            with pytest.raises(ValueError, match="avg_completion_rate must be between 0 and 100"):
                await service.save_creator_snapshot(creator_id=uuid4(), avg_completion_rate=bad_val)


class TestContentPerformanceValidation:
    @pytest.mark.asyncio
    async def test_valid_update(self, service):
        cid = uuid4()
        await service.content_repo.create(content_id=cid)
        await service.update_content_performance(
            content_id=cid,
            views_7d=10,
            views_30d=20,
            avg_completion_pct=50.0,
            revenue_7d=1.0,
            revenue_30d=2.0,
        )
        m = await service.content_repo.get_by_content(cid)
        assert m.views_7d == 10

    @pytest.mark.asyncio
    async def test_rejects_negative_counters(self, service):
        cid = uuid4()
        await service.content_repo.create(content_id=cid)
        for label, bad_val in [
            ("views_7d", -1),
            ("views_30d", -1),
            ("revenue_7d", -1.0),
            ("revenue_30d", -1.0),
        ]:
            with pytest.raises(ValueError, match=f"{label} cannot be negative"):
                await service.update_content_performance(content_id=cid, **{label: bad_val})

    @pytest.mark.asyncio
    async def test_rejects_invalid_avg_completion(self, service):
        cid = uuid4()
        await service.content_repo.create(content_id=cid)
        for bad_val in [-1.0, 101.0, float("nan"), float("inf")]:
            with pytest.raises(ValueError, match="avg_completion_pct must be between 0 and 100"):
                await service.update_content_performance(content_id=cid, avg_completion_pct=bad_val)


class TestEventDeduplication:
    @pytest.mark.asyncio
    async def test_log_event_dedup(self, service, dedup_store):
        """Duplicate client_event_id for same user is suppressed."""
        uid = uuid4()
        cid = "client-123"
        await service.log_event(uid, "e1", client_event_id=cid)
        await service.log_event(uid, "e1", client_event_id=cid)
        # second call suppressed
        assert len(service.event_repo.events) == 1

    @pytest.mark.asyncio
    async def test_log_event_dedup_different_users(self, service, dedup_store):
        """Same client_event_id for different users is allowed."""
        cid = "client-123"
        await service.log_event(uuid4(), "e1", client_event_id=cid)
        await service.log_event(uuid4(), "e1", client_event_id=cid)
        assert len(service.event_repo.events) == 2

    @pytest.mark.asyncio
    async def test_log_event_dedup_different_ids(self, service, dedup_store):
        """Different client_event_ids are always allowed."""
        uid = uuid4()
        await service.log_event(uid, "e1", client_event_id="cid-1")
        await service.log_event(uid, "e1", client_event_id="cid-2")
        assert len(service.event_repo.events) == 2

    @pytest.mark.asyncio
    async def test_view_event_dedup(self, service, dedup_store):
        """Duplicate client_event_id for same viewer is suppressed."""
        vid = uuid4()
        cid = "view-456"
        await service.record_view_event(
            content_id=uuid4(),
            viewer_id=vid,
            watch_duration_seconds=10,
            content_duration_seconds=300,
            completion_pct=5.0,
            client_event_id=cid,
        )
        await service.record_view_event(
            content_id=uuid4(),
            viewer_id=vid,
            watch_duration_seconds=10,
            content_duration_seconds=300,
            completion_pct=5.0,
            client_event_id=cid,
        )
        assert len(service.view_repo.events) == 1

    @pytest.mark.asyncio
    async def test_dedup_fails_open_when_store_unavailable(self, service):
        """When dedup store raises, events still get recorded."""
        service.dedup_store = MagicMock()
        service.dedup_store.set = AsyncMock(side_effect=RuntimeError("redis down"))
        uid = uuid4()
        await service.log_event(uid, "e1", client_event_id="cid")
        await service.log_event(uid, "e1", client_event_id="cid")
        assert len(service.event_repo.events) == 2

    @pytest.mark.asyncio
    async def test_dedup_rejects_long_client_event_id(self, service):
        uid = uuid4()
        with pytest.raises(ValueError, match="client_event_id must be"):
            await service.log_event(uid, "e1", client_event_id="x" * 201)


class TestAggregationIsolation:
    """Verify repository queries cannot mix users/creators/content."""

    @pytest.mark.asyncio
    async def test_get_user_events_filters_by_user(self, service):
        u1, u2 = uuid4(), uuid4()
        await service.event_repo.create(u1, "e1")
        await service.event_repo.create(u2, "e2")
        await service.event_repo.create(u1, "e3")
        events = await service.get_user_events(u1, limit=100)
        assert len(events) == 2
        assert all(e["event_type"] in ("e1", "e3") for e in events)

    @pytest.mark.asyncio
    async def test_creator_snapshots_keyed_by_creator(self, service):
        c1, c2 = uuid4(), uuid4()
        await service.creator_repo.create(creator_id=c1, total_views=10)
        await service.creator_repo.create(creator_id=c2, total_views=20)
        s1 = await service.get_creator_analytics(c1)
        s2 = await service.get_creator_analytics(c2)
        assert s1["total_views"] == 10
        assert s2["total_views"] == 20

    @pytest.mark.asyncio
    async def test_content_metrics_keyed_by_content(self, service):
        c1, c2 = uuid4(), uuid4()
        await service.content_repo.create(content_id=c1, views_7d=5)
        await service.content_repo.create(content_id=c2, views_7d=15)
        m1 = await service.get_content_performance(c1)
        m2 = await service.get_content_performance(c2)
        assert m1["views_7d"] == 5
        assert m2["views_7d"] == 15

    @pytest.mark.asyncio
    async def test_view_events_keyed_by_content_and_viewer(self, service):
        cid1, cid2 = uuid4(), uuid4()
        vid1, vid2 = uuid4(), uuid4()
        await service.view_repo.create(content_id=cid1, viewer_id=vid1, watch_duration_seconds=10)
        await service.view_repo.create(content_id=cid2, viewer_id=vid2, watch_duration_seconds=20)
        v1 = await service.view_repo.get_by_content(cid1)
        v2 = await service.view_repo.get_by_viewer(vid2)
        assert len(v1) == 1 and v1[0].content_id == cid1
        assert len(v2) == 1 and v2[0].viewer_id == vid2
