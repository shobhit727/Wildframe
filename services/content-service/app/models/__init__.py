"""
SQLAlchemy ORM models for Content Service.
Manages animation content, episodes, seasons, genres, series, and recommendations.
"""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


# Association table for many-to-many relationship between Content and Genre
content_genre_association = Table(
    "content_genre",
    Base.metadata,
    Column("content_id", UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE")),
    Column("genre_id", UUID(as_uuid=True), ForeignKey("genre.id", ondelete="CASCADE")),
    UniqueConstraint("content_id", "genre_id", name="_content_genre_uc"),
)

# Association table for many-to-many relationship between Content and Cast
content_cast_association = Table(
    "content_cast",
    Base.metadata,
    Column("content_id", UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE")),
    Column("cast_id", UUID(as_uuid=True), ForeignKey("cast_member.id", ondelete="CASCADE")),
    Column("role", String(255), nullable=False),
    UniqueConstraint("content_id", "cast_id", "role", name="_content_cast_uc"),
)


class ContentType(str, Enum):
    """Content type enumeration."""

    MOVIE = "movie"
    SERIES = "series"
    DOCUMENTARY = "documentary"
    # Animation-specific content types
    SHORT_FILM = "short_film"
    FEATURE_FILM = "feature_film"
    EPISODE = "episode"
    ANIMATIC = "animatic"
    STORYBOARD = "storyboard"


class AnimationStyle(str, Enum):
    """Animation style enumeration."""

    TRADITIONAL_2D = "traditional_2d"
    CGI_3D = "cgi_3d"
    STOP_MOTION = "stop_motion"
    MOTION_GRAPHICS = "motion_graphics"
    HYBRID = "hybrid"
    PIXEL_ART = "pixel_art"


class ContentStatus(str, Enum):
    """Content status enumeration."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class SeriesStatus(str, Enum):
    """Series status enumeration."""

    ONGOING = "ongoing"
    COMPLETED = "completed"
    HIATUS = "hiatus"


class Content(Base):
    """Represents movies, series, episodes, and other animation content."""

    __tablename__ = "content"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False)
    content_type = Column(SQLEnum(ContentType), nullable=False, index=True)  # type: ignore[var-annotated]
    status = Column(SQLEnum(ContentStatus), nullable=False, default=ContentStatus.DRAFT)  # type: ignore[var-annotated]

    # Animation-specific fields
    animation_style = Column(SQLEnum(AnimationStyle), nullable=True, index=True)  # type: ignore[var-annotated]
    maturity_rating = Column(
        String(20), nullable=True
    )  # G, PG, PG-13, R, TV-Y, TV-Y7, TV-PG, TV-14, TV-MA
    dub_languages = Column(JSONB, default=[])  # List of language codes available for dubbing
    subtitle_languages = Column(JSONB, default=[])  # List of language codes available for subtitles
    creator_id = Column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # FK to creators-service (external)
    is_original = Column(
        Boolean, default=False, nullable=False
    )  # True if original IP, False if licensed
    premiere_date = Column(DateTime, nullable=True)  # Festival or platform premiere date

    # Series/episode hierarchy (nullable for non-episodic content)
    episode_number = Column(Integer, nullable=True)
    season_number = Column(Integer, nullable=True)
    series_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_series.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Metadata
    release_date = Column(DateTime, nullable=True, index=True)
    duration_minutes = Column(Integer, nullable=True)  # For movies/episodes
    original_language = Column(String(10), nullable=False, default="en")
    country = Column(String(100), nullable=True)

    # Media references
    poster_url = Column(String(500), nullable=True)
    backdrop_url = Column(String(500), nullable=True)
    trailer_url = Column(String(500), nullable=True)

    # Ratings and engagement
    imdb_rating = Column(Float, nullable=True)
    audience_score = Column(Float, default=0.0)  # 0-100 from user ratings
    total_votes = Column(Integer, default=0)

    # Content info
    content_rating = Column(String(20), nullable=True)  # G, PG, PG-13, R, etc.
    is_premium = Column(Boolean, default=False)
    can_download = Column(Boolean, default=True)
    can_stream = Column(Boolean, default=True)

    # Metadata tags
    tags = Column(JSONB, default={})  # {"production_company": "...", "director": "..."}

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    published_at = Column(DateTime, nullable=True)

    # Relationships
    genres: Mapped[list["Genre"]] = relationship(
        "Genre", secondary=content_genre_association, back_populates="content"
    )
    cast_members: Mapped[list["CastMember"]] = relationship(
        "CastMember", secondary=content_cast_association, back_populates="content"
    )
    seasons: Mapped[list["Season"]] = relationship(
        "Season", back_populates="content", cascade="all, delete-orphan"
    )
    episodes: Mapped[list["Episode"]] = relationship(
        "Episode", back_populates="content", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["ContentRating"]] = relationship(
        "ContentRating", back_populates="content", cascade="all, delete-orphan"
    )
    recommendations: Mapped[list["ContentRecommendation"]] = relationship(
        "ContentRecommendation",
        back_populates="content",
        cascade="all, delete-orphan",
        foreign_keys="ContentRecommendation.content_id",
    )
    series: Mapped["ContentSeries"] = relationship(
        "ContentSeries", back_populates="episodes", foreign_keys=[series_id]
    )
    creators: Mapped[list["ContentCreator"]] = relationship(
        "ContentCreator", back_populates="content", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_content_type_status", "content_type", "status"),
        Index("ix_content_maturity_rating", "maturity_rating"),
    )


class Season(Base):
    """Represents seasons in a series."""

    __tablename__ = "season"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Media
    poster_url = Column(String(500), nullable=True)

    # Release info
    release_date = Column(DateTime, nullable=True)
    episode_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    content: Mapped["Content"] = relationship("Content", back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(
        "Episode", back_populates="season", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("content_id", "season_number", name="_season_content_number_uc"),
        Index("ix_season_content_number", "content_id", "season_number"),
    )


class Episode(Base):
    """Represents episodes in a series."""

    __tablename__ = "episode"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(
        UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("season.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Duration and media
    duration_minutes = Column(Integer, nullable=False)
    thumbnail_url = Column(String(500), nullable=True)

    # Release info
    release_date = Column(DateTime, nullable=True)
    is_available = Column(Boolean, default=True)

    # Content rating
    audience_score = Column(Float, default=0.0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    content: Mapped["Content"] = relationship("Content", back_populates="episodes")
    season: Mapped["Season"] = relationship("Season", back_populates="episodes")

    __table_args__ = (
        UniqueConstraint("season_id", "episode_number", name="_episode_season_number_uc"),
        Index("ix_episode_season_number", "season_id", "episode_number"),
    )


class Genre(Base):
    """Represents content genres."""

    __tablename__ = "genre"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True, index=True)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    icon_url = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    content: Mapped["Content"] = relationship(
        "Content", secondary=content_genre_association, back_populates="genres"
    )


class CastMember(Base):
    """Represents cast members (actors, directors, producers, etc.)."""

    __tablename__ = "cast_member"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    bio = Column(Text, nullable=True)
    birth_date = Column(DateTime, nullable=True)
    image_url = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    content: Mapped["Content"] = relationship(
        "Content", secondary=content_cast_association, back_populates="cast_members"
    )


class ContentRating(Base):
    """Stores individual user ratings for content."""

    __tablename__ = "content_rating"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(
        UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    rating = Column(Float, nullable=False)  # 0-10
    review = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    content: Mapped["Content"] = relationship("Content", back_populates="ratings")

    __table_args__ = (
        UniqueConstraint("content_id", "user_id", name="_content_rating_user_uc"),
        Index("ix_content_rating_user", "content_id", "user_id"),
    )


class ContentRecommendation(Base):
    """Stores content recommendations (similar content)."""

    __tablename__ = "content_recommendation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(
        UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recommended_content_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True
    )

    similarity_score = Column(Float, nullable=False)  # 0-1
    recommendation_type = Column(String(50), nullable=False)  # 'similar', 'sequel', 'prequel', etc.

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    content: Mapped["Content"] = relationship(
        "Content", back_populates="recommendations", foreign_keys=[content_id]
    )

    __table_args__ = (
        UniqueConstraint("content_id", "recommended_content_id", name="_content_recommendation_uc"),
        Index("ix_content_recommendation_score", "content_id", "similarity_score"),
    )


class ContentCreator(Base):
    """Represents a creator credited on an animation content item.
    Maps a creator (from creators-service) to a content item with a role.
    """

    __tablename__ = "content_creator"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(
        UUID(as_uuid=True), ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True
    )
    creator_id = Column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # FK to creators-service (external)
    role = Column(String(50), nullable=False)  # animator, director, writer, producer, etc.
    credit_order = Column(Integer, default=0, nullable=False)  # Display order in credits

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    content: Mapped["Content"] = relationship("Content", back_populates="creators")

    __table_args__ = (
        UniqueConstraint("content_id", "creator_id", "role", name="_content_creator_uc"),
    )


class ContentSeries(Base):
    """Represents an animation series with its metadata.
    Episodes are linked to a series via Content.series_id.
    """

    __tablename__ = "content_series"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    animation_style = Column(SQLEnum(AnimationStyle), nullable=True, index=True)  # type: ignore[var-annotated]

    total_seasons = Column(Integer, default=0, nullable=False)
    total_episodes = Column(Integer, default=0, nullable=False)
    status = Column(String(20), nullable=False, default=SeriesStatus.ONGOING.value)

    creator_id = Column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # FK to creators-service (external)

    # Media
    poster_url = Column(String(500), nullable=True)
    backdrop_url = Column(String(500), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    episodes: Mapped[list["Episode"]] = relationship(
        "Content", back_populates="series", foreign_keys="Content.series_id"
    )

    __table_args__ = (Index("ix_content_series_status", "status"),)
