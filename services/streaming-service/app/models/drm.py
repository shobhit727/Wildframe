"""Streaming DRM models - FairPlay Widevine device limits expiry."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class DRMConfig(Base):
    __tablename__ = "drm_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True, unique=True
    )
    fairplay_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    widevine_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    expiry_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    offline_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
