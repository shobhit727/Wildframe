import uuid
from datetime import UTC, datetime
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
class Base(DeclarativeBase): pass
class TrackingConsent(Base):
    __tablename__ = "tracking_consents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    cookie_consent: Mapped[str] = mapped_column(String(20), default="essential", nullable=False)
    sdk_governed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consent_mode: Mapped[str] = mapped_column(String(20), default="denied", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
