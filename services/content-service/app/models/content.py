"""Content service database models."""

from datetime import datetime, timezone
from uuid import uuid4
from enum import Enum

from sqlalchemy import String, Integer, Float, Boolean, DateTime, Enum as SQLEnum, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship

Base = declarative_base()


class ContentType(str, Enum):
    """Content type enumeration."""
    MOVIE = "movie"
    SHOW = "show"
    DOCUMENTARY = "documentary"
    SHORT = "short"


class AgeRating(str, Enum):
    """Content age rating."""
    G = "G"
    PG = "PG"
    PG_13 = "PG-13"
    R = "R"
    NC_17 = "NC-17"
    NOT_RATED = "NR"


class Genre(Base):
    """Content genre."""
    __tablename__ = "genres"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class Movie(Base):
    """Movie content."""
    __tablename__ = "movies"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    poster_url: Mapped[str] = mapped_column(String(500), nullable=False)
    backdrop_url: Mapped[str] = mapped_column(String(500), nullable=True)
    release_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vote_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    age_rating: Mapped[str] = mapped_column(SQLEnum(AgeRating), default=AgeRating.NOT_RATED, nullable=False)
    genre_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    director: Mapped[str] = mapped_column(String(255), nullable=False)
    cast: Mapped[list] = mapped_column(ARRAY(String), nullable=True)
    budget: Mapped[int] = mapped_column(Integer, nullable=True)
    revenue: Mapped[int] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    production_companies: Mapped[list] = mapped_column(ARRAY(String), nullable=True)
    trailer_url: Mapped[str] = mapped_column(String(500), nullable=True)
    media_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    
    __table_args__ = (
        Index("idx_movie_title_active", "title", "is_active"),
        Index("idx_movie_release_date", "release_date"),
        Index("idx_movie_rating", "rating"),
    )


class Show(Base):
    """TV show content."""
    __tablename__ = "shows"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    poster_url: Mapped[str] = mapped_column(String(500), nullable=False)
    backdrop_url: Mapped[str] = mapped_column(String(500), nullable=True)
    first_air_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_air_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    episode_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    total_seasons: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    total_episodes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vote_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    age_rating: Mapped[str] = mapped_column(SQLEnum(AgeRating), default=AgeRating.NOT_RATED, nullable=False)
    genre_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    creators: Mapped[list] = mapped_column(ARRAY(String), nullable=True)
    cast: Mapped[list] = mapped_column(ARRAY(String), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    production_companies: Mapped[list] = mapped_column(ARRAY(String), nullable=True)
    is_ongoing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trailer_url: Mapped[str] = mapped_column(String(500), nullable=True)
    media_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    
    __table_args__ = (
        Index("idx_show_title_active", "title", "is_active"),
        Index("idx_show_first_air_date", "first_air_date"),
        Index("idx_show_rating", "rating"),
    )


class Season(Base):
    """TV show season."""
    __tablename__ = "seasons"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    show_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), ForeignKey("shows.id"), nullable=False, index=True)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    poster_url: Mapped[str] = mapped_column(String(500), nullable=True)
    air_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    episode_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    
    __table_args__ = (
        UniqueConstraint("show_id", "season_number", name="uq_show_season_number"),
        Index("idx_season_show_number", "show_id", "season_number"),
    )


class Episode(Base):
    """TV show episode."""
    __tablename__ = "episodes"
    
    id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    season_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), ForeignKey("seasons.id"), nullable=False, index=True)
    show_id: Mapped[uuid4] = mapped_column(UUID(as_uuid=True), ForeignKey("shows.id"), nullable=False, index=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[str] = mapped_column(String(500), nullable=False)
    air_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vote_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    media_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    
    __table_args__ = (
        UniqueConstraint("season_id", "episode_number", name="uq_season_episode_number"),
        Index("idx_episode_show_season", "show_id", "season_id"),
        Index("idx_episode_air_date", "air_date"),
    )
