import uuid

"""Uploads service models.

Two tables drive the chunked/resumable upload flow:

* ``upload_sessions``  — one row per upload; tracks status, size, chunk plan,
  checksum and the final storage key once the object is assembled.
* ``upload_chunks``    — one row per received chunk; the set of received chunk
  indices is what ``complete_session`` verifies before declaring success.

Status machine (``UploadSession.status``):

    initiated → uploading → complete
                     ↘ aborted

A session is created ``initiated``. The first received chunk flips it to
``uploading``. ``complete_session`` verifies every expected chunk is present and
the assembled checksum matches, then flips to ``complete`` and emits
``content.uploaded``. ``abort`` (or expiry) flips to ``aborted`` and emits
``content.uploaded.aborted``.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class UploadSessionStatus(str, Enum):
    INITIATED = "initiated"
    UPLOADING = "uploading"
    COMPLETE = "complete"
    ABORTED = "aborted"


class OutboxEventStatus(str, Enum):
    """Delivery state of an outbox row (at-least-once event publishing)."""

    PENDING = "pending"
    DISPATCHED = "dispatched"


class UploadSession(Base):
    """A chunked/resumable upload session."""

    __tablename__ = "upload_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[UploadSessionStatus] = mapped_column(
        SQLEnum(UploadSessionStatus),
        default=UploadSessionStatus.INITIATED,
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    multipart_upload_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Set when object-storage cleanup for this session has fully succeeded
    # (sessions aborted or expired re-run cleanup until this is set).
    storage_cleaned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_upload_session_creator", "creator_id", "status"),
        Index("idx_upload_session_expires", "expires_at"),
    )


class UploadChunk(Base):
    """A single received chunk of an upload session."""

    __tablename__ = "upload_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("upload_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Server-verified digest of the stored chunk bytes (from storage
    # metadata), never a client assertion.
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        # A chunk index is unique per session — receiving the same index twice is
        # a client bug / replay and must not double-count.
        Index("idx_upload_chunk_session_index", "session_id", "index", unique=True),
    )


class OutboxEvent(Base):
    """Transactional outbox row: an event persisted in the same DB transaction
    as the state change that produced it (dual-write avoidance). A background
    worker drains PENDING rows to the event bus and marks them DISPATCHED.
    """

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    topic: Mapped[str] = mapped_column(String(127), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[OutboxEventStatus] = mapped_column(
        SQLEnum(OutboxEventStatus),
        default=OutboxEventStatus.PENDING,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
