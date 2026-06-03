"""Media pipeline service models."""
from datetime import datetime, timezone
from uuid import uuid4
from enum import Enum
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Enum as SQLEnum, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class TranscodingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class TranscodingJob(Base):
    """Video transcoding job."""
    __tablename__ = "transcoding_jobs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    source_url = Column(String(2048), nullable=False)
    status = Column(SQLEnum(TranscodingStatus), default=TranscodingStatus.PENDING)
    progress_percentage = Column(Integer, default=0)
    output_hls_url = Column(String(2048), nullable=True)
    output_dash_url = Column(String(2048), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("idx_transcoding_status", "status"),)
