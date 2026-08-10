"""Analytics service repositories."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ContentPerformanceMetrics,
    ContentViewEvent,
    CreatorAnalyticsSnapshot,
    Event,
)


class EventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        event_type: str,
        event_data: dict | None = None,
        content_id: UUID | None = None,
    ):
        event = Event(
            user_id=user_id, event_type=event_type, event_data=event_data, content_id=content_id
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_by_user(self, user_id: UUID, limit: int = 100) -> list[Event]:
        stmt = select(Event).where(Event.user_id == user_id).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ContentViewEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        content_id: UUID,
        viewer_id: UUID,
        watch_duration_seconds: int = 0,
        content_duration_seconds: int = 0,
        completion_pct: float = 0.0,
        playback_quality: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> ContentViewEvent:
        event = ContentViewEvent(
            content_id=content_id,
            viewer_id=viewer_id,
            watch_duration_seconds=watch_duration_seconds,
            content_duration_seconds=content_duration_seconds,
            completion_pct=completion_pct,
            playback_quality=playback_quality,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_by_content(self, content_id: UUID, limit: int = 100) -> list[ContentViewEvent]:
        stmt = (
            select(ContentViewEvent)
            .where(ContentViewEvent.content_id == content_id)
            .order_by(ContentViewEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_viewer(self, viewer_id: UUID, limit: int = 100) -> list[ContentViewEvent]:
        stmt = (
            select(ContentViewEvent)
            .where(ContentViewEvent.viewer_id == viewer_id)
            .order_by(ContentViewEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class CreatorAnalyticsSnapshotRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        creator_id: UUID,
        total_views: int = 0,
        total_watch_hours: float = 0.0,
        avg_completion_rate: float = 0.0,
        unique_viewers: int = 0,
        revenue_earned: float = 0.0,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> CreatorAnalyticsSnapshot:
        snapshot = CreatorAnalyticsSnapshot(
            creator_id=creator_id,
            total_views=total_views,
            total_watch_hours=total_watch_hours,
            avg_completion_rate=avg_completion_rate,
            unique_viewers=unique_viewers,
            revenue_earned=revenue_earned,
            period_start=period_start,
            period_end=period_end,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def get_latest_for_creator(self, creator_id: UUID) -> CreatorAnalyticsSnapshot | None:
        stmt = (
            select(CreatorAnalyticsSnapshot)
            .where(CreatorAnalyticsSnapshot.creator_id == creator_id)
            .order_by(CreatorAnalyticsSnapshot.period_end.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_for_creator_in_range(
        self, creator_id: UUID, period_start: datetime, period_end: datetime
    ) -> list[CreatorAnalyticsSnapshot]:
        stmt = (
            select(CreatorAnalyticsSnapshot)
            .where(
                and_(
                    CreatorAnalyticsSnapshot.creator_id == creator_id,
                    CreatorAnalyticsSnapshot.period_start >= period_start,
                    CreatorAnalyticsSnapshot.period_end <= period_end,
                )
            )
            .order_by(CreatorAnalyticsSnapshot.period_start)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ContentPerformanceMetricsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        content_id: UUID,
        views_7d: int = 0,
        views_30d: int = 0,
        avg_completion_pct: float = 0.0,
        revenue_7d: float = 0.0,
        revenue_30d: float = 0.0,
    ) -> ContentPerformanceMetrics:
        metrics = ContentPerformanceMetrics(
            content_id=content_id,
            views_7d=views_7d,
            views_30d=views_30d,
            avg_completion_pct=avg_completion_pct,
            revenue_7d=revenue_7d,
            revenue_30d=revenue_30d,
        )
        self.session.add(metrics)
        await self.session.flush()
        return metrics

    async def get_by_content(self, content_id: UUID) -> ContentPerformanceMetrics | None:
        stmt = select(ContentPerformanceMetrics).where(
            ContentPerformanceMetrics.content_id == content_id
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def update_metrics(
        self,
        content_id: UUID,
        views_7d: int | None = None,
        views_30d: int | None = None,
        avg_completion_pct: float | None = None,
        revenue_7d: float | None = None,
        revenue_30d: float | None = None,
    ) -> ContentPerformanceMetrics | None:
        metrics = await self.get_by_content(content_id)
        if not metrics:
            return None
        if views_7d is not None:
            metrics.views_7d = views_7d
        if views_30d is not None:
            metrics.views_30d = views_30d
        if avg_completion_pct is not None:
            metrics.avg_completion_pct = avg_completion_pct
        if revenue_7d is not None:
            metrics.revenue_7d = revenue_7d
        if revenue_30d is not None:
            metrics.revenue_30d = revenue_30d
        await self.session.flush()
        return metrics
