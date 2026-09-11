import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class TrackingConsent(Base):
    __tablename__ = "tracking_consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    cookie_consent: Mapped[str] = mapped_column(String(20), default="essential", nullable=False)
    sdk_governed: Mapped[bool] = mapped_column(default=True, nullable=False)
    consent_mode: Mapped[str] = mapped_column(String(20), default="denied", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if getattr(self, "cookie_consent", None) is None:
            self.cookie_consent = "essential"
        if getattr(self, "sdk_governed", None) is None:
            self.sdk_governed = True
        if getattr(self, "consent_mode", None) is None:
            self.consent_mode = "denied"
        if getattr(self, "created_at", None) is None:
            self.created_at = datetime.now(UTC)
