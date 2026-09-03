"""Content-service rights registry - territorial licensing, avail management, conflict detection."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RightsHolder(Base):
    __tablename__ = "rights_holders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # creator, studio, distributor
    contact: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class TerritorialLicense(Base):
    __tablename__ = "territorial_licenses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    rights_holder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    territory: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True
    )  # US, EU, IN, GLOBAL
    exclusive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    avail_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    avail_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    royalty_rate: Mapped[str] = mapped_column(String(20), default="0.30", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("idx_license_content_territory", "content_id", "territory"),)
