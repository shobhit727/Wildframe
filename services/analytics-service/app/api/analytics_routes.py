"""Analytics service API routes."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import (
    ContentPerformanceMetricsRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
    EventRepository,
)
from app.services import AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user id from the JWT sub claim."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )


async def require_self(
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    request: Request,
) -> UUID:
    """Ensure the path user_id matches the authenticated user."""
    path_user_id = request.path_params.get("user_id")
    if path_user_id is None or str(path_user_id) == str(jwt_user_id):
        return jwt_user_id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You can only access your own data",
    )


async def get_analytics_service(
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AnalyticsService:
    return AnalyticsService(
        EventRepository(db),
        ContentViewEventRepository(db),
        CreatorAnalyticsSnapshotRepository(db),
        ContentPerformanceMetricsRepository(db),
    )


@router.post("/events", response_model=dict)
async def log_event(
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
    user_id: UUID = Body(...),
    event_type: str = Body(...),
    event_data: dict | None = Body(None),  # noqa: B008
    content_id: UUID | None = Body(None),  # noqa: B008
):
    """Log analytics event."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only log your own events"
        )
    await service.log_event(user_id, event_type, event_data, content_id)
    return {"status": "logged"}


@router.get("/user-events/{user_id}")
async def get_user_events(
    user_id: Annotated[UUID, Depends(require_self)],
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
    limit: int = 100,
):
    """Get user events."""
    events = await service.get_user_events(user_id, limit)
    return {"events": events, "total": len(events)}


@router.post("/view-events", response_model=dict)
async def record_view_event(
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
    content_id: UUID = Body(...),
    viewer_id: UUID = Body(...),
    watch_duration_seconds: int = Body(0),
    content_duration_seconds: int = Body(0),
    completion_pct: float = Body(0.0),
    playback_quality: str | None = Body(None),
    started_at: datetime | None = Body(None),  # noqa: B008
    completed_at: datetime | None = Body(None),  # noqa: B008
):
    """Record a content view/playback event."""
    if viewer_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only record your own view events",
        )
    await service.record_view_event(
        content_id=content_id,
        viewer_id=viewer_id,
        watch_duration_seconds=watch_duration_seconds,
        content_duration_seconds=content_duration_seconds,
        completion_pct=completion_pct,
        playback_quality=playback_quality,
        started_at=started_at,
        completed_at=completed_at,
    )
    return {"status": "recorded"}


@router.get("/creators/{creator_id}")
async def get_creator_analytics(
    creator_id: UUID, service: AnalyticsService = Depends(get_analytics_service)  # noqa: B008
):
    """Get analytics for a creator."""
    analytics = await service.get_creator_analytics(creator_id)
    if not analytics:
        return {"creator_id": str(creator_id), "analytics": None}
    return analytics


@router.get("/content/{content_id}")
async def get_content_performance(
    content_id: UUID, service: AnalyticsService = Depends(get_analytics_service)  # noqa: B008
):
    """Get performance metrics for content."""
    performance = await service.get_content_performance(content_id)
    if not performance:
        return {"content_id": str(content_id), "metrics": None}
    return performance
