"""Analytics-service DSAR models - export and retention compliance."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AnalyticsDSARExport(Base):
    """Analytics data export for DSAR - events, sessions, tracking."""

    __tablename__ = "analytics_dsar_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    dsar_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    export_format: Mapped[str] = mapped_column(String(10), default="json", nullable=False)  # json, csv
    retention_days: Mapped[int] = mapped_column(default=365, nullable=False)
    sla_compliant: Mapped[bool] = mapped_column(default=True, nullable=False)  # 30d GDPR /45d CCPA check
    data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array of events
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (Index("idx_analytics_dsar_user", "user_id"),)
