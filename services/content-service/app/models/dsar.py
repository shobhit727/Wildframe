"""Content-service DSAR models - copyright metadata and usage rights export."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ContentDSARRecord(Base):
    """Content-specific DSAR - export copyright metadata, viewing rights, uploads."""

    __tablename__ = "content_dsar_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    dsar_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # FK to user-service DSAR
    content_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # viewing_history, uploads, reviews, rights
    export_data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON export
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("idx_content_dsar_user", "user_id"),)
