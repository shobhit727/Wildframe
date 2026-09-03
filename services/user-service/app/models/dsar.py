"""User-service DSAR models - Data Subject Access Request workflow."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class DSARRequest(Base):
    """DSAR request workflow - access, portability, correction, deletion."""

    __tablename__ = "dsar_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # access, portability, correction, deletion, restriction, objection, automated_decision
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)  # pending, verified, processing, completed, rejected
    data_categories: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # 30d GDPR / 45d CCPA
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_location: Mapped[str | None] = mapped_column(Text, nullable=True)  # S3 path for export
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (
        Index("idx_dsar_user_status", "user_id", "status"),
        Index("idx_dsar_sla", "sla_deadline"),
    )
