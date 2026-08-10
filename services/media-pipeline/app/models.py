import uuid

"""Media pipeline service models.

The pipeline is an event-driven state machine over ``pipeline_jobs``. Each job
moves through a fixed ordered set of stages (see ``app/core/stages.py``); every
attempt at a stage is recorded in ``pipeline_stage_log`` so the pipeline is
fully auditable and replayable.

PipelineJob.status machine::

    pending → running → completed
                ↘ failed   (a critical stage exhausted retries → DLQ)

``current_stage`` is the stage the job is currently on (or last attempted).
``stage_versions`` is a JSONB map of stage_name -> output summary, so a
resumed job can skip stages already done. ``retries`` counts attempts at the
*current* stage (reset when the job advances past it).
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column, Mapped, declarative_base

Base = declarative_base()


class PipelineJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"


class PipelineStageStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineJob(Base):
    """A single piece of content moving through the animation pipeline."""

    __tablename__ = "pipeline_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    upload_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    current_stage = Column(String(100), nullable=True)
    status = Column(
        SQLEnum(PipelineJobStatus),
        default=PipelineJobStatus.PENDING,
        nullable=False,
        index=True,
    )
    # stage_name -> {output summary, completed_at}. Lets a resumed job skip work.
    stage_versions = Column(JSONB, nullable=False, default=dict)
    # Number of attempts at ``current_stage`` so far (drives retry/backoff).
    retries = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)
    # Per-job mutable state passed between stages. Persisted as JSONB so the
    # orchestrator can resume across requests (see app.services.advance()).
    context = Column(JSONB, nullable=False, default=dict)
    started_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_pipeline_job_status", "status"),
        Index("idx_pipeline_job_upload_session", "upload_session_id"),
    )


class PipelineStageLog(Base):
    """One attempt at one stage of one job. Append-only audit trail."""

    __tablename__ = "pipeline_stage_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(100), nullable=False)
    status = Column(SQLEnum(PipelineStageStatus), nullable=False)
    duration_ms = Column(Integer, nullable=False, default=0)
    message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_stage_log_job", "job_id", "created_at"),
        Index("idx_stage_log_stage", "stage"),
    )


# ---------------------------------------------------------------------------
# Legacy compatibility model.
#
# The previous media-pipeline exposed a ``TranscodingJob`` model and a
# ``/media/transcode`` route. We keep the name defined (not re-exported) so
# anything that imported it still resolves; the canonical model is now
# ``PipelineJob``. Kept minimal and unused by the new pipeline.
# ---------------------------------------------------------------------------


class TranscodingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TranscodingJob(Base):
    """Legacy video transcoding job (kept for import compatibility)."""

    __tablename__ = "transcoding_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    source_url = Column(String(2048), nullable=False)
    status = Column(SQLEnum(TranscodingStatus), default=TranscodingStatus.PENDING)
    progress_percentage = Column(Integer, default=0)
    output_hls_url = Column(String(2048), nullable=True)
    output_dash_url = Column(String(2048), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    __table_args__ = (Index("idx_transcoding_status", "status"),)
