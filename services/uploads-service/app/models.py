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
from datetime import datetime, timezone
from uuid import uuid4
from enum import Enum
from sqlalchemy import (
    Column,
    String,
    Integer,
    BigInteger,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class UploadSessionStatus(str, Enum):
    INITIATED = "initiated"
    UPLOADING = "uploading"
    COMPLETE = "complete"
    ABORTED = "aborted"


class UploadSession(Base):
    """A chunked/resumable upload session."""

    __tablename__ = "upload_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    mime = Column(String(127), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    status = Column(
        SQLEnum(UploadSessionStatus),
        default=UploadSessionStatus.INITIATED,
        nullable=False,
        index=True,
    )
    storage_key = Column(String(1024), nullable=True)
    checksum_sha256 = Column(String(64), nullable=True)
    chunk_size = Column(BigInteger, nullable=False)
    total_chunks = Column(Integer, nullable=False)
    uploaded_chunks = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_upload_session_creator", "creator_id", "status"),
        Index("idx_upload_session_expires", "expires_at"),
    )


class UploadChunk(Base):
    """A single received chunk of an upload session."""

    __tablename__ = "upload_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("upload_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    index = Column(Integer, nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    etag = Column(String(255), nullable=True)
    received_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        # A chunk index is unique per session — receiving the same index twice is
        # a client bug / replay and must not double-count.
        Index("idx_upload_chunk_session_index", "session_id", "index", unique=True),
    )
