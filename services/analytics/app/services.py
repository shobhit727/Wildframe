"""Analytics service business logic."""
from datetime import datetime
from uuid import UUID

from app.repositories import (
    ContentPerformanceMetricsRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
    EventRepository,
)


class AnalyticsService:
    def __init__(
        self,
        event_repo: EventRepository,
        view_repo: ContentViewEventRepository,
        creator_repo: CreatorAnalyticsSnapshotRepository,
        content_repo: ContentPerformanceMetricsRepository,
    ):
        self.event_repo = event_repo
        self.view_repo = view_repo
        self.creator_repo = creator_repo
        self.content_repo = content_repo

    # Generic event logging (kept for backward compatibility)

    async def log_event(self, user_id: UUID, event_type: str, event_data: dict | None = None, content_id: UUID | None = None):
        """Log analytics event."""
        return await self.event_repo.create(user_id, event_type, event_data, content_id)

    async def get_user_events(self, user_id: UUID, limit: int = 100):
        """Get user events."""
        events = await self.event_repo.get_by_user(user_id, limit)
        return [{"event_type": e.event_type, "data": e.event_data, "timestamp": e.timestamp.isoformat()} for e in events]

    # View event tracking

    async def record_view_event(
        self,
        content_id: UUID,
        viewer_id: UUID,
        watch_duration_seconds: int = 0,
        content_duration_seconds: int = 0,
        completion_pct: float = 0.0,
        playback_quality: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ):
        """Record a content view/playback event."""
        return await self.view_repo.create(
            content_id=content_id,
            viewer_id=viewer_id,
            watch_duration_seconds=watch_duration_seconds,
            content_duration_seconds=content_duration_seconds,
            completion_pct=completion_pct,
            playback_quality=playback_quality,
            started_at=started_at,
            completed_at=completed_at,
        )

    # Creator analytics

    async def get_creator_analytics(self, creator_id: UUID) -> dict | None:
        """Get the latest analytics snapshot for a creator."""
        snapshot = await self.creator_repo.get_latest_for_creator(creator_id)
        if not snapshot:
            return None
        return {
            "creator_id": str(snapshot.creator_id),
            "total_views": snapshot.total_views,
            "total_watch_hours": snapshot.total_watch_hours,
            "avg_completion_rate": snapshot.avg_completion_rate,
            "unique_viewers": snapshot.unique_viewers,
            "revenue_earned": snapshot.revenue_earned,
            "period_start": snapshot.period_start.isoformat() if snapshot.period_start else None,
            "period_end": snapshot.period_end.isoformat() if snapshot.period_end else None,
        }

    async def save_creator_snapshot(
        self,
        creator_id: UUID,
        total_views: int = 0,
        total_watch_hours: float = 0.0,
        avg_completion_rate: float = 0.0,
        unique_viewers: int = 0,
        revenue_earned: float = 0.0,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ):
        """Save a creator analytics snapshot."""
        return await self.creator_repo.create(
            creator_id=creator_id,
            total_views=total_views,
            total_watch_hours=total_watch_hours,
            avg_completion_rate=avg_completion_rate,
            unique_viewers=unique_viewers,
            revenue_earned=revenue_earned,
            period_start=period_start,
            period_end=period_end,
        )

    # Content performance metrics

    async def get_content_performance(self, content_id: UUID) -> dict | None:
        """Get performance metrics for a content item."""
        metrics = await self.content_repo.get_by_content(content_id)
        if not metrics:
            return None
        return {
            "content_id": str(metrics.content_id),
            "views_7d": metrics.views_7d,
            "views_30d": metrics.views_30d,
            "avg_completion_pct": metrics.avg_completion_pct,
            "revenue_7d": metrics.revenue_7d,
            "revenue_30d": metrics.revenue_30d,
            "updated_at": metrics.updated_at.isoformat() if metrics.updated_at else None,
        }

    async def update_content_performance(
        self,
        content_id: UUID,
        views_7d: int | None = None,
        views_30d: int | None = None,
        avg_completion_pct: float | None = None,
        revenue_7d: float | None = None,
        revenue_30d: float | None = None,
    ):
        """Update content performance metrics."""
        return await self.content_repo.update_metrics(
            content_id=content_id,
            views_7d=views_7d,
            views_30d=views_30d,
            avg_completion_pct=avg_completion_pct,
            revenue_7d=revenue_7d,
            revenue_30d=revenue_30d,
        )
