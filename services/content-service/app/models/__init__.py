"""
SQLAlchemy ORM models for Content Service.
Manages content, episodes, seasons, genres, and recommendations.
"""

from sqlalchemy import (
    Column, String, Integer, Float, Text, Boolean, DateTime,
    ForeignKey, Table, UniqueConstraint, Index, Enum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import uuid
import enum

Base = declarative_base()

# Association table for many-to-many relationship between Content and Genre
content_genre_association = Table(
    'content_genre',
    Base.metadata,
    Column('content_id', UUID(as_uuid=True), ForeignKey('content.id', ondelete='CASCADE')),
    Column('genre_id', UUID(as_uuid=True), ForeignKey('genre.id', ondelete='CASCADE')),
    UniqueConstraint('content_id', 'genre_id', name='_content_genre_uc')
)

# Association table for many-to-many relationship between Content and Cast
content_cast_association = Table(
    'content_cast',
    Base.metadata,
    Column('content_id', UUID(as_uuid=True), ForeignKey('content.id', ondelete='CASCADE')),
    Column('cast_id', UUID(as_uuid=True), ForeignKey('cast_member.id', ondelete='CASCADE')),
    Column('role', String(255), nullable=False),
    UniqueConstraint('content_id', 'cast_id', 'role', name='_content_cast_uc')
)


class ContentType(str, enum.Enum):
    """Content type enumeration."""
    MOVIE = "movie"
    SERIES = "series"
    DOCUMENTARY = "documentary"


class ContentStatus(str, enum.Enum):
    """Content status enumeration."""
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Content(Base):
    """Represents movies, series, and other content."""
    __tablename__ = 'content'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False)
    content_type = Column(Enum(ContentType), nullable=False, index=True)
    status = Column(Enum(ContentStatus), nullable=False, default=ContentStatus.DRAFT)
    
    # Metadata
    release_date = Column(DateTime, nullable=True, index=True)
    duration_minutes = Column(Integer, nullable=True)  # For movies
    original_language = Column(String(10), nullable=False, default='en')
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
    genres = relationship(
        'Genre',
        secondary=content_genre_association,
        back_populates='content'
    )
    cast_members = relationship(
        'CastMember',
        secondary=content_cast_association,
        back_populates='content'
    )
    seasons = relationship('Season', back_populates='content', cascade='all, delete-orphan')
    episodes = relationship('Episode', back_populates='content', cascade='all, delete-orphan')
    ratings = relationship('ContentRating', back_populates='content', cascade='all, delete-orphan')
    recommendations = relationship(
        'ContentRecommendation',
        back_populates='content',
        cascade='all, delete-orphan',
        foreign_keys='ContentRecommendation.content_id'
    )
    
    __table_args__ = (
        Index('ix_content_type_status', 'content_type', 'status'),
        Index('ix_content_release_date', 'release_date'),
    )


class Season(Base):
    """Represents seasons in a series."""
    __tablename__ = 'season'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(UUID(as_uuid=True), ForeignKey('content.id', ondelete='CASCADE'), nullable=False, index=True)
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
    content = relationship('Content', back_populates='seasons')
    episodes = relationship('Episode', back_populates='season', cascade='all, delete-orphan')
    
    __table_args__ = (
        UniqueConstraint('content_id', 'season_number', name='_season_content_number_uc'),
        Index('ix_season_content_number', 'content_id', 'season_number'),
    )


class Episode(Base):
    """Represents episodes in a series."""
    __tablename__ = 'episode'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(UUID(as_uuid=True), ForeignKey('content.id', ondelete='CASCADE'), nullable=False, index=True)
    season_id = Column(UUID(as_uuid=True), ForeignKey('season.id', ondelete='CASCADE'), nullable=False, index=True)
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
    content = relationship('Content', back_populates='episodes')
    season = relationship('Season', back_populates='episodes')
    
    __table_args__ = (
        UniqueConstraint('season_id', 'episode_number', name='_episode_season_number_uc'),
        Index('ix_episode_season_number', 'season_id', 'episode_number'),
    )


class Genre(Base):
    """Represents content genres."""
    __tablename__ = 'genre'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True, index=True)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    icon_url = Column(String(500), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    content = relationship(
        'Content',
        secondary=content_genre_association,
        back_populates='genres'
    )


class CastMember(Base):
    """Represents cast members (actors, directors, producers, etc.)."""
    __tablename__ = 'cast_member'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    bio = Column(Text, nullable=True)
    birth_date = Column(DateTime, nullable=True)
    image_url = Column(String(500), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    content = relationship(
        'Content',
        secondary=content_cast_association,
        back_populates='cast_members'
    )


class ContentRating(Base):
    """Stores individual user ratings for content."""
    __tablename__ = 'content_rating'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(UUID(as_uuid=True), ForeignKey('content.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    rating = Column(Float, nullable=False)  # 0-10
    review = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    content = relationship('Content', back_populates='ratings')
    
    __table_args__ = (
        UniqueConstraint('content_id', 'user_id', name='_content_rating_user_uc'),
        Index('ix_content_rating_user', 'content_id', 'user_id'),
    )


class ContentRecommendation(Base):
    """Stores content recommendations (similar content)."""
    __tablename__ = 'content_recommendation'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id = Column(UUID(as_uuid=True), ForeignKey('content.id', ondelete='CASCADE'), nullable=False, index=True)
    recommended_content_id = Column(UUID(as_uuid=True), ForeignKey('content.id', ondelete='CASCADE'), nullable=False, index=True)
    
    similarity_score = Column(Float, nullable=False)  # 0-1
    recommendation_type = Column(String(50), nullable=False)  # 'similar', 'sequel', 'prequel', etc.
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    content = relationship(
        'Content',
        back_populates='recommendations',
        foreign_keys=[content_id]
    )
    
    __table_args__ = (
        UniqueConstraint('content_id', 'recommended_content_id', name='_content_recommendation_uc'),
        Index('ix_content_recommendation_score', 'content_id', 'similarity_score'),
    )
