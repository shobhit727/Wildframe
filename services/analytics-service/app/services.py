"""Analytics service business logic."""

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.repositories import (
    ContentPerformanceMetricsRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
    EventRepository,
)

logger = logging.getLogger(__name__)

# Integrity constants enforced at the ingestion boundary.
MAX_EVENT_LIMIT = 1000
MAX_EVENT_TYPE_LENGTH = 100
MAX_EVENT_DATA_BYTES = 10_000
MAX_CLIENT_EVENT_ID_LENGTH = 200
MAX_EVENT_DATA_DEPTH = 10
MAX_DURATION_SECONDS = 24 * 60 * 60  # single playback session cannot exceed a day
TIMESTAMP_SKEW = timedelta(minutes=5)  # tolerated client clock skew
ALLOWED_PLAYBACK_QUALITIES = frozenset({"240p", "360p", "480p", "720p", "1080p", "4k"})
DEDUP_TTL_SECONDS = 86_400


def _check_depth(value: Any, depth: int = 0) -> None:
    """Reject excessively nested event_data to prevent stack exhaustion."""
    if depth > MAX_EVENT_DATA_DEPTH:
        raise ValueError(f"event_data nesting depth exceeds {MAX_EVENT_DATA_DEPTH}")
    if isinstance(value, dict):
        for v in value.values():
            _check_depth(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _check_depth(v, depth + 1)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize naive/other-tz timestamps to UTC-aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _check_finite(value: Any) -> None:
    """Reject NaN/infinity anywhere in event payloads (JSON allows the
    literals; they poison aggregates silently)."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers are not allowed in event data")
    if isinstance(value, dict):
        for v in value.values():
            _check_finite(v)
    elif isinstance(value, list):
        for v in value:
            _check_finite(v)


class AnalyticsService:
    def __init__(
        self,
        event_repo: EventRepository,
        view_repo: ContentViewEventRepository,
        creator_repo: CreatorAnalyticsSnapshotRepository,
        content_repo: ContentPerformanceMetricsRepository,
        dedup_store: Any = None,
    ):
        self.event_repo = event_repo
        self.view_repo = view_repo
        self.creator_repo = creator_repo
        self.content_repo = content_repo
        # Optional async key-value store (redis-compatible `set(..., nx=True,
        # ex=...)`) used for client_event_id idempotency. Fail-open when absent.
        self.dedup_store = dedup_store

    # Generic event logging (kept for backward compatibility)

    async def log_event(
        self,
        user_id: UUID,
        event_type: str,
        event_data: dict | None = None,
        content_id: UUID | None = None,
        client_event_id: str | None = None,
    ):
        """Log analytics event."""
        event_type = (event_type or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        if len(event_type) > MAX_EVENT_TYPE_LENGTH:
            raise ValueError(f"event_type exceeds {MAX_EVENT_TYPE_LENGTH} characters")
        if event_data is not None:
            _check_finite(event_data)
            _check_depth(event_data)
            if len(json.dumps(event_data, default=str)) > MAX_EVENT_DATA_BYTES:
                raise ValueError(f"event_data exceeds {MAX_EVENT_DATA_BYTES} bytes")
        if await self._is_duplicate("event", user_id, client_event_id):
            return None
        return await self.event_repo.create(user_id, event_type, event_data, content_id)

    async def get_user_events(self, user_id: UUID, limit: int = 100):
        """Get user events."""
        if not 1 <= limit <= MAX_EVENT_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_EVENT_LIMIT}")
        events = await self.event_repo.get_by_user(user_id, limit)
        return [
            {"event_type": e.event_type, "data": e.event_data, "timestamp": e.timestamp.isoformat()}
            for e in events
        ]

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
        client_event_id: str | None = None,
    ):
        """Record a content view/playback event.

        Validates metric bounds and normalizes timestamps before persisting,
        so impossible/future-dated events cannot distort aggregates.
        """
        if watch_duration_seconds < 0 or content_duration_seconds < 0:
            raise ValueError("Durations cannot be negative")
        if (
            watch_duration_seconds > MAX_DURATION_SECONDS
            or content_duration_seconds > MAX_DURATION_SECONDS
        ):
            raise ValueError(f"Durations cannot exceed {MAX_DURATION_SECONDS} seconds")
        if not math.isfinite(completion_pct) or not 0.0 <= completion_pct <= 100.0:
            raise ValueError("completion_pct must be a finite number between 0 and 100")
        if content_duration_seconds > 0 and watch_duration_seconds > content_duration_seconds:
            raise ValueError("watch_duration_seconds cannot exceed content_duration_seconds")
        if playback_quality is not None:
            playback_quality = playback_quality.strip().lower()
            if playback_quality not in ALLOWED_PLAYBACK_QUALITIES:
                raise ValueError(f"Unsupported playback_quality: {playback_quality}")
        started_at = _as_utc(started_at)
        completed_at = _as_utc(completed_at)
        now = datetime.now(UTC)
        for label, ts in (("started_at", started_at), ("completed_at", completed_at)):
            if ts is not None and ts > now + TIMESTAMP_SKEW:
                raise ValueError(f"{label} cannot be in the future")
        if started_at is not None and completed_at is not None and completed_at < started_at:
            raise ValueError("completed_at cannot precede started_at")
        if await self._is_duplicate("view", viewer_id, client_event_id):
            return None
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
        if total_views < 0 or unique_viewers < 0 or revenue_earned < 0 or total_watch_hours < 0:
            raise ValueError("Snapshot counters cannot be negative")
        if not math.isfinite(avg_completion_rate) or not 0.0 <= avg_completion_rate <= 100.0:
            raise ValueError("avg_completion_rate must be between 0 and 100")
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
        for label, value in (
            ("views_7d", views_7d),
            ("views_30d", views_30d),
            ("revenue_7d", revenue_7d),
            ("revenue_30d", revenue_30d),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{label} cannot be negative")
        if avg_completion_pct is not None and (
            not math.isfinite(avg_completion_pct) or not 0.0 <= avg_completion_pct <= 100.0
        ):
            raise ValueError("avg_completion_pct must be between 0 and 100")
        return await self.content_repo.update_metrics(
            content_id=content_id,
            views_7d=views_7d,
            views_30d=views_30d,
            avg_completion_pct=avg_completion_pct,
            revenue_7d=revenue_7d,
            revenue_30d=revenue_30d,
        )

    # Event idempotency

    async def _is_duplicate(self, scope: str, owner_id: UUID, client_event_id: str | None) -> bool:
        """Best-effort client_event_id dedup. Fail-open: store errors never
        drop events."""
        if not client_event_id or self.dedup_store is None:
            return False
        client_event_id = client_event_id.strip()
        if not client_event_id or len(client_event_id) > MAX_CLIENT_EVENT_ID_LENGTH:
            raise ValueError(f"client_event_id must be ≤ {MAX_CLIENT_EVENT_ID_LENGTH} characters")
        key = f"wf:analytics:dedup:{scope}:{owner_id}:{client_event_id}"
        try:
            return not await self.dedup_store.set(key, "1", nx=True, ex=DEDUP_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.warning("Analytics dedup store unavailable; proceeding without dedup")
            return False
