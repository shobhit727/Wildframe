"""Content service Pydantic schemas."""

from datetime import datetime
from uuid import UUID
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class AgeRatingEnum(str, Enum):
    """Age rating enumeration."""
    G = "G"
    PG = "PG"
    PG_13 = "PG-13"
    R = "R"
    NC_17 = "NC-17"
    NOT_RATED = "NR"


class ContentTypeEnum(str, Enum):
    """Content type enumeration."""
    MOVIE = "movie"
    SHOW = "show"
    DOCUMENTARY = "documentary"
    SHORT = "short"


class GenreResponse(BaseModel):
    """Genre response."""
    id: UUID
    name: str
    description: Optional[str] = None
    created_at: datetime
    is_active: bool
    
    class Config:
        from_attributes = True


class CreateGenreRequest(BaseModel):
    """Create genre request."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class MovieResponse(BaseModel):
    """Movie response."""
    id: UUID
    title: str
    description: str
    poster_url: str
    backdrop_url: Optional[str] = None
    release_date: datetime
    duration_seconds: int
    rating: float = Field(..., ge=0.0, le=10.0)
    vote_count: int
    age_rating: AgeRatingEnum
    genre_ids: List[UUID]
    director: str
    cast: Optional[List[str]] = None
    budget: Optional[int] = None
    revenue: Optional[int] = None
    language: str
    production_companies: Optional[List[str]] = None
    trailer_url: Optional[str] = None
    views_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class CreateMovieRequest(BaseModel):
    """Create movie request."""
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=10)
    poster_url: str = Field(..., min_length=5)
    backdrop_url: Optional[str] = None
    release_date: datetime
    duration_seconds: int = Field(..., gt=0)
    age_rating: AgeRatingEnum = AgeRatingEnum.NOT_RATED
    genre_ids: List[UUID] = Field(..., min_length=1)
    director: str = Field(..., min_length=1, max_length=255)
    cast: Optional[List[str]] = None
    budget: Optional[int] = None
    revenue: Optional[int] = None
    language: str = Field(default="en", min_length=2, max_length=10)
    production_companies: Optional[List[str]] = None
    trailer_url: Optional[str] = None
    media_key: str = Field(..., min_length=5)
    
    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v):
        if v < 60:
            raise ValueError("Duration must be at least 60 seconds")
        if v > 86400:
            raise ValueError("Duration cannot exceed 24 hours")
        return v


class UpdateMovieRequest(BaseModel):
    """Update movie request."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, min_length=10)
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    rating: Optional[float] = Field(None, ge=0.0, le=10.0)
    age_rating: Optional[AgeRatingEnum] = None
    genre_ids: Optional[List[UUID]] = None
    cast: Optional[List[str]] = None
    budget: Optional[int] = None
    revenue: Optional[int] = None
    trailer_url: Optional[str] = None


class ShowResponse(BaseModel):
    """Show response."""
    id: UUID
    title: str
    description: str
    poster_url: str
    backdrop_url: Optional[str] = None
    first_air_date: datetime
    last_air_date: Optional[datetime] = None
    episode_runtime_seconds: int
    total_seasons: int
    total_episodes: int
    rating: float = Field(..., ge=0.0, le=10.0)
    vote_count: int
    age_rating: AgeRatingEnum
    genre_ids: List[UUID]
    creators: Optional[List[str]] = None
    cast: Optional[List[str]] = None
    language: str
    production_companies: Optional[List[str]] = None
    is_ongoing: bool
    trailer_url: Optional[str] = None
    views_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class CreateShowRequest(BaseModel):
    """Create show request."""
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=10)
    poster_url: str = Field(..., min_length=5)
    backdrop_url: Optional[str] = None
    first_air_date: datetime
    episode_runtime_seconds: int = Field(..., gt=0)
    age_rating: AgeRatingEnum = AgeRatingEnum.NOT_RATED
    genre_ids: List[UUID] = Field(..., min_length=1)
    creators: Optional[List[str]] = None
    cast: Optional[List[str]] = None
    language: str = Field(default="en", min_length=2, max_length=10)
    production_companies: Optional[List[str]] = None
    is_ongoing: bool = True
    trailer_url: Optional[str] = None
    media_key: str = Field(..., min_length=5)


class SeasonResponse(BaseModel):
    """Season response."""
    id: UUID
    show_id: UUID
    season_number: int
    title: Optional[str] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    air_date: Optional[datetime] = None
    episode_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class CreateSeasonRequest(BaseModel):
    """Create season request."""
    show_id: UUID
    season_number: int = Field(..., gt=0)
    title: Optional[str] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    air_date: Optional[datetime] = None


class EpisodeResponse(BaseModel):
    """Episode response."""
    id: UUID
    season_id: UUID
    show_id: UUID
    episode_number: int
    title: str
    description: str
    thumbnail_url: str
    air_date: datetime
    duration_seconds: int
    rating: float = Field(..., ge=0.0, le=10.0)
    vote_count: int
    views_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class CreateEpisodeRequest(BaseModel):
    """Create episode request."""
    season_id: UUID
    show_id: UUID
    episode_number: int = Field(..., gt=0)
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=10)
    thumbnail_url: str = Field(..., min_length=5)
    air_date: datetime
    duration_seconds: int = Field(..., gt=0)
    media_key: str = Field(..., min_length=5)
    
    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v):
        if v < 60:
            raise ValueError("Duration must be at least 60 seconds")
        if v > 43200:
            raise ValueError("Duration cannot exceed 12 hours")
        return v


class ListMoviesResponse(BaseModel):
    """List movies response."""
    movies: List[MovieResponse]
    total: int
    page: int = 1
    page_size: int = 20


class ListShowsResponse(BaseModel):
    """List shows response."""
    shows: List[ShowResponse]
    total: int
    page: int = 1
    page_size: int = 20


class ListSeasonsResponse(BaseModel):
    """List seasons response."""
    seasons: List[SeasonResponse]
    total: int


class ListEpisodesResponse(BaseModel):
    """List episodes response."""
    episodes: List[EpisodeResponse]
    total: int


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    status_code: int
