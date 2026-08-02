import re

"""
Pydantic v2 schemas for Content Service API requests/responses.
"""


from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class GenreResponse(BaseModel):
    """Genre response schema."""

    id: UUID
    name: str
    slug: str
    description: str | None = None
    icon_url: str | None = None

    model_config = {"from_attributes": True}


class CastMemberResponse(BaseModel):
    """Cast member response schema."""

    id: UUID
    name: str
    slug: str
    bio: str | None = None
    birth_date: datetime | None = None
    image_url: str | None = None

    model_config = {"from_attributes": True}


class EpisodeResponse(BaseModel):
    """Episode response schema."""

    id: UUID
    episode_number: int
    title: str
    description: str | None = None
    duration_minutes: int
    thumbnail_url: str | None = None
    release_date: datetime | None = None
    is_available: bool
    audience_score: float

    model_config = {"from_attributes": True}


class SeasonResponse(BaseModel):
    """Season response schema."""

    id: UUID
    season_number: int
    title: str
    description: str | None = None
    poster_url: str | None = None
    release_date: datetime | None = None
    episode_count: int
    episodes: list[EpisodeResponse] = []

    model_config = {"from_attributes": True}


class ContentRatingResponse(BaseModel):
    """Content rating response schema."""

    id: UUID
    user_id: UUID
    rating: float
    review: str | None = None
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
    release_date: datetime | None = None
    duration_minutes: int | None = None
    original_language: str
    country: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    trailer_url: str | None = None
    imdb_rating: float | None = None
    audience_score: float
    total_votes: int
    content_rating: str | None = None
    is_premium: bool
    can_download: bool
    can_stream: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None

    genres: list[GenreResponse] = []
    cast_members: list[CastMemberResponse] = []
    seasons: list[SeasonResponse] = []

    model_config = {"from_attributes": True}


class ContentListResponse(BaseModel):
    """Simplified content response for list endpoints."""

    id: UUID
    title: str
    slug: str
    description: str
    content_type: str
    poster_url: str | None = None
    imdb_rating: float | None = None
    audience_score: float
    is_premium: bool
    genres: list[GenreResponse] = []

    model_config = {"from_attributes": True}


# Request schemas


class GenreCreateRequest(BaseModel):
    """Genre creation request schema."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    icon_url: str | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v


class CastMemberCreateRequest(BaseModel):
    """Cast member creation request schema."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255)
    bio: str | None = None
    birth_date: datetime | None = None
    image_url: str | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v


class EpisodeCreateRequest(BaseModel):
    """Episode creation request schema."""

    episode_number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int = Field(..., ge=1)
    thumbnail_url: str | None = None
    release_date: datetime | None = None
    is_available: bool = True


class EpisodeUpdateRequest(BaseModel):
    """Episode update request schema."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int | None = Field(None, ge=1)
    thumbnail_url: str | None = None
    release_date: datetime | None = None
    is_available: bool | None = None


class SeasonCreateRequest(BaseModel):
    """Season creation request schema."""

    season_number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    poster_url: str | None = None
    release_date: datetime | None = None


class SeasonUpdateRequest(BaseModel):
    """Season update request schema."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    poster_url: str | None = None
    release_date: datetime | None = None


class ContentCreateRequest(BaseModel):
    """Content creation request schema."""

    title: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    content_type: str = Field(..., pattern="^(movie|series|documentary)$")
    release_date: datetime | None = None
    duration_minutes: int | None = Field(None, ge=1)
    original_language: str = Field(default="en", max_length=10)
    country: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    trailer_url: str | None = None
    imdb_rating: float | None = Field(None, ge=0, le=10)
    content_rating: str | None = None
    is_premium: bool = False
    can_download: bool = True
    can_stream: bool = True
    genre_ids: list[UUID] = []

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
        return v


class ContentUpdateRequest(BaseModel):
    """Content update request schema."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    release_date: datetime | None = None
    duration_minutes: int | None = Field(None, ge=1)
    country: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    trailer_url: str | None = None
    imdb_rating: float | None = Field(None, ge=0, le=10)
    content_rating: str | None = None
    is_premium: bool | None = None
    can_download: bool | None = None
    can_stream: bool | None = None
    genre_ids: list[UUID] | None = None


class ContentPublishRequest(BaseModel):
    """Content publish request schema."""

    status: str = Field(..., pattern="^(published|archived|draft)$")


class ContentRatingCreateRequest(BaseModel):
    """Content rating creation request schema."""

    rating: float = Field(..., ge=0, le=10)
    review: str | None = None


class ContentRecommendationCreateRequest(BaseModel):
    """Content recommendation creation request schema."""

    recommended_content_id: UUID
    similarity_score: float = Field(..., ge=0, le=1)
    recommendation_type: str = Field(..., max_length=50)


class ErrorResponse(BaseModel):
    """Error response schema."""

    status_code: int
    message: str
    detail: str | None = None


class HealthCheckResponse(BaseModel):
    """Health check response schema."""

    status: str
    version: str
    database: str
