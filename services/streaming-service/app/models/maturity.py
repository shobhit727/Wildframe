"""Streaming-service maturity models - content gating by maturity rating (AVMS)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ContentMaturity(Base):
    """Content maturity rating and parental controls."""

    __tablename__ = "content_maturity"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True, unique=True)
    maturity_rating: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # G, PG, PG-13, R, NC-17, 18+
    min_age: Mapped[int] = mapped_column(Integer, nullable=False)  # 0, 7, 13, 16, 18
    requires_parental_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purchase_restricted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spending_limit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screen_time_limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bedtime_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "21:00"
    bedtime_end: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "07:00"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (Index("idx_maturity_rating", "maturity_rating"),)
