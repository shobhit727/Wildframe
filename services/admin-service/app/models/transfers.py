import uuid
from datetime import UTC, datetime
from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
class Base(DeclarativeBase): pass
class TransferRecord(Base):
    __tablename__ = "transfer_records"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_region: Mapped[str] = mapped_column(String(10), nullable=False)
    target_region: Mapped[str] = mapped_column(String(10), nullable=False)
    mechanism: Mapped[str] = mapped_column(String(20), default="SCC", nullable=False)
    adequacy: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
