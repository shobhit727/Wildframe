import uuid

"""Analytics service models."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, Mapped, declarative_base

Base = declarative_base()


class Event(Base):
    """Analytics event log."""

    __tablename__ = "events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)
    event_data = Column(JSON)
    content_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(UTC), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    __table_args__ = (Index("idx_events_user_type", "user_id", "event_type"),)


class ContentViewEvent(Base):
    """Tracks a single view/playback session for content."""

    __tablename__ = "content_view_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    viewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    watch_duration_seconds = Column(Integer, nullable=False, default=0)
    content_duration_seconds = Column(Integer, nullable=False, default=0)
    completion_pct = Column(Float, nullable=False, default=0.0)  # 0-100
    playback_quality = Column(String(20), nullable=True)  # 240p, 360p, 480p, 720p, 1080p, 4k
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    __table_args__ = (
        Index("idx_view_events_content", "content_id", "created_at"),
        Index("idx_view_events_viewer", "viewer_id", "created_at"),
    )


class CreatorAnalyticsSnapshot(Base):
    """Aggregated analytics snapshot for a creator over a time period."""

    __tablename__ = "creator_analytics_snapshots"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    total_views = Column(Integer, nullable=False, default=0)
    total_watch_hours = Column(Float, nullable=False, default=0.0)
    avg_completion_rate = Column(Float, nullable=False, default=0.0)  # 0-100
    unique_viewers = Column(Integer, nullable=False, default=0)
    revenue_earned = Column(Float, nullable=False, default=0.0)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    __table_args__ = (Index("idx_creator_analytics_creator", "creator_id", "period_end"),)


class ContentPerformanceMetrics(Base):
    """Performance metrics for a specific content item over rolling windows."""

    __tablename__ = "content_performance_metrics"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    views_7d = Column(Integer, nullable=False, default=0)
    views_30d = Column(Integer, nullable=False, default=0)
    avg_completion_pct = Column(Float, nullable=False, default=0.0)  # 0-100
    revenue_7d = Column(Float, nullable=False, default=0.0)
    revenue_30d = Column(Float, nullable=False, default=0.0)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    __table_args__ = (
        Index("idx_content_perf_views_7d", "views_7d"),
        Index("idx_content_perf_views_30d", "views_30d"),
    )
