import uuid

"""Search service models."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_naive_now = lambda: datetime.now(UTC).replace(tzinfo=None)  # noqa: E731


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class SearchQuery(Base):
    """Search query log for analytics."""

    __tablename__ = "search_queries"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    query_text = Column(String(500), nullable=False)
    result_count = Column(Integer, default=0)
    filters = Column(JSON, nullable=True)
    clicked_result_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=_naive_now)
    __table_args__ = (Index("idx_search_user_date", "user_id", "created_at"),)


class SearchIndex(Base):
    """Elasticsearch index metadata."""

    __tablename__ = "search_indexes"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    title = Column(String(500), nullable=False)
    description = Column(Text)
    content_type = Column(String(50), nullable=False)
    genres = Column(JSON)
    actors = Column(JSON)
    director = Column(String(200))
    release_year = Column(Integer)
    rating = Column(Integer)
    # Animation-specific fields
    animation_style = Column(String(50), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    maturity_rating = Column(String(20), nullable=True)
    dub_languages = Column(JSON, nullable=True)
    subtitle_languages = Column(JSON, nullable=True)
    indexed_at = Column(DateTime, default=_naive_now)
    updated_at = Column(DateTime, default=_naive_now, onupdate=_naive_now)
