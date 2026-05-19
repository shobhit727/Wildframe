"""
Pydantic v2 schemas for Content Service API requests/responses.
"""

from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from uuid import UUID
from typing import Optional, List
import re


class GenreResponse(BaseModel):
    """Genre response schema."""
    id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    
    model_config = {"from_attributes": True}


class CastMemberResponse(BaseModel):
    """Cast member response schema."""
    id: UUID
    name: str
    slug: str
    bio: Optional[str] = None
    birth_date: Optional[datetime] = None
    image_url: Optional[str] = None
    
    model_config = {"from_attributes": True}


class EpisodeResponse(BaseModel):
    """Episode response schema."""
    id: UUID
    episode_number: int
    title: str
    description: Optional[str] = None
    duration_minutes: int
    thumbnail_url: Optional[str] = None
    release_date: Optional[datetime] = None
    is_available: bool
    audience_score: float
    
    model_config = {"from_attributes": True}


class SeasonResponse(BaseModel):
    """Season response schema."""
    id: UUID
    season_number: int
    title: str
    description: Optional[str] = None
    poster_url: Optional[str] = None
    release_date: Optional[datetime] = None
    episode_count: int
    episodes: List[EpisodeResponse] = []
    
    model_config = {"from_attributes": True}


class ContentRatingResponse(BaseModel):
    """Content rating response schema."""
    id: UUID
    user_id: UUID
    rating: float
    review: Optional[str] = None
    created_at: datetime
    
    model_config = {"from_attributes": True}


class ContentRecommendationResponse(BaseModel):
    """Content recommendation response schema."""
    id: UUID
    recommended_content_id: UUID
    similarity_score: float
    recommendation_type: str
    
    model_config = {"from_attributes": True}


class ContentResponse(BaseModel):
    """Full content response schema."""
    id: UUID
    title: str
    slug: str
    description: str
    content_type: str
    status: str
    release_date: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    original_language: str
    country: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    trailer_url: Optional[str] = None
    imdb_rating: Optional[float] = None
    audience_score: float
    total_votes: int
    content_rating: Optional[str] = None
    is_premium: bool
    can_download: bool
    can_stream: bool
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None
    
    genres: List[GenreResponse] = []
    cast_members: List[CastMemberResponse] = []
    seasons: List[SeasonResponse] = []
    
    model_config = {"from_attributes": True}


class ContentListResponse(BaseModel):
    """Simplified content response for list endpoints."""
    id: UUID
    title: str
    slug: str
    description: str
    content_type: str
    poster_url: Optional[str] = None
    imdb_rating: Optional[float] = None
    audience_score: float
    is_premium: bool
    genres: List[GenreResponse] = []
    
    model_config = {"from_attributes": True}


# Request schemas

class GenreCreateRequest(BaseModel):
    """Genre creation request schema."""
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    icon_url: Optional[str] = None
    
    @field_validator('slug')
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', v):
            raise ValueError('Slug must contain only lowercase letters, numbers, and hyphens')
        return v


class CastMemberCreateRequest(BaseModel):
    """Cast member creation request schema."""
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255)
    bio: Optional[str] = None
    birth_date: Optional[datetime] = None
    image_url: Optional[str] = None
    
    @field_validator('slug')
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', v):
            raise ValueError('Slug must contain only lowercase letters, numbers, and hyphens')
        return v


class EpisodeCreateRequest(BaseModel):
    """Episode creation request schema."""
    episode_number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    duration_minutes: int = Field(..., ge=1)
    thumbnail_url: Optional[str] = None
    release_date: Optional[datetime] = None
    is_available: bool = True


class EpisodeUpdateRequest(BaseModel):
    """Episode update request schema."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=1)
    thumbnail_url: Optional[str] = None
    release_date: Optional[datetime] = None
    is_available: Optional[bool] = None


class SeasonCreateRequest(BaseModel):
    """Season creation request schema."""
    season_number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    poster_url: Optional[str] = None
    release_date: Optional[datetime] = None


class SeasonUpdateRequest(BaseModel):
    """Season update request schema."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    poster_url: Optional[str] = None
    release_date: Optional[datetime] = None


class ContentCreateRequest(BaseModel):
    """Content creation request schema."""
    title: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    content_type: str = Field(..., regex='^(movie|series|documentary)$')
    release_date: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, ge=1)
    original_language: str = Field(default='en', max_length=10)
    country: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    trailer_url: Optional[str] = None
    imdb_rating: Optional[float] = Field(None, ge=0, le=10)
    content_rating: Optional[str] = None
    is_premium: bool = False
    can_download: bool = True
    can_stream: bool = True
    genre_ids: List[UUID] = []
    
    @field_validator('slug')
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', v):
            raise ValueError('Slug must contain only lowercase letters, numbers, and hyphens')
        return v


class ContentUpdateRequest(BaseModel):
    """Content update request schema."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    release_date: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, ge=1)
    country: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    trailer_url: Optional[str] = None
    imdb_rating: Optional[float] = Field(None, ge=0, le=10)
    content_rating: Optional[str] = None
    is_premium: Optional[bool] = None
    can_download: Optional[bool] = None
    can_stream: Optional[bool] = None
    genre_ids: Optional[List[UUID]] = None


class ContentPublishRequest(BaseModel):
    """Content publish request schema."""
    status: str = Field(..., regex='^(published|archived|draft)$')


class ContentRatingCreateRequest(BaseModel):
    """Content rating creation request schema."""
    rating: float = Field(..., ge=0, le=10)
    review: Optional[str] = None


class ContentRecommendationCreateRequest(BaseModel):
    """Content recommendation creation request schema."""
    recommended_content_id: UUID
    similarity_score: float = Field(..., ge=0, le=1)
    recommendation_type: str = Field(..., max_length=50)


class ErrorResponse(BaseModel):
    """Error response schema."""
    status_code: int
    message: str
    detail: Optional[str] = None


class HealthCheckResponse(BaseModel):
    """Health check response schema."""
    status: str
    version: str
    database: str
