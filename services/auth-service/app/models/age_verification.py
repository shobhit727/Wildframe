"""Auth-service age verification models - self-declare plus document check, consent_minor_age per jurisdiction."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, BaseModel


class AgeVerification(Base, BaseModel):
    """Age verification record - links to user, stores verification method and result."""

    __tablename__ = "age_verifications"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    verification_method: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # self_declare, document, id_check
    declared_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    consent_minor_age: Mapped[int] = mapped_column(Integer, nullable=False)  # 16 EU, 13 US, 18 IN
    document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_age_verify_user", "user_id", unique=True),
        Index("idx_age_verify_minor", "is_minor"),
    )
