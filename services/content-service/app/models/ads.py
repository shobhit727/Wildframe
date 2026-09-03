"""Ads models - consent-gated, minor-safe."""
import uuid
from datetime import UTC, datetime
from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
class Base(DeclarativeBase): pass
class AdConfig(Base):
    __tablename__ = "ad_configs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    consent_gated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    minor_safe: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tcf_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
