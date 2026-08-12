"""Analytics service API routes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from jose import JWTError, jwt  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import (
    ContentPerformanceMetricsRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
    EventRepository,
)
from app.services import MAX_EVENT_LIMIT, AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_DEDUP_REDIS: redis.Redis | None = None


def _dedup_store() -> redis.Redis | None:
    """Lazily create the shared redis client used for event idempotency."""
    global _DEDUP_REDIS
    if _DEDUP_REDIS is None:
        _DEDUP_REDIS = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _DEDUP_REDIS


@dataclass
class UserContext:
    """Authenticated principal resolved from the JWT claims."""

    user_id: UUID
    role: str | None = None


async def get_current_user_context(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UserContext:
    """Resolve the authenticated user id and role from the JWT claims.

    Identity and role always come from the token, never from caller-supplied
    path/query/body values.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    try:
        user_id = UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    return UserContext(user_id=user_id, role=payload.get("role"))


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user id from the JWT sub claim."""
    return (await get_current_user_context(authorization)).user_id


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


async def require_self_or_admin(
    context: Annotated[UserContext, Depends(get_current_user_context)],
    request: Request,
) -> UUID:
    """Allow access when the path resource belongs to the caller, or the
    caller holds the admin role. Guards /creators/{creator_id}."""
    if context.role == "admin":
        return context.user_id
    path_user_id = request.path_params.get("creator_id") or request.path_params.get("user_id")
    if path_user_id is not None and str(path_user_id) == str(context.user_id):
        return context.user_id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You can only access your own data",
    )


async def require_admin(
    context: Annotated[UserContext, Depends(get_current_user_context)],
) -> UUID:
    """Allow access only to callers holding the admin role.

    Content performance cannot be tied back to an owning creator from
    analytics data alone, so it is gated to admins rather than inherited by
    ordinary users.
    """
    if context.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return context.user_id


async def get_analytics_service(
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AnalyticsService:
    return AnalyticsService(
        EventRepository(db),
        ContentViewEventRepository(db),
        CreatorAnalyticsSnapshotRepository(db),
        ContentPerformanceMetricsRepository(db),
        dedup_store=_dedup_store(),
    )


def _invalid_metrics(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/events", response_model=dict)
async def log_event(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    user_id: UUID = Body(...),
    event_type: str = Body(...),
    event_data: dict | None = Body(None),  # noqa: B008
    content_id: UUID | None = Body(None),  # noqa: B008
    client_event_id: str | None = Body(None),  # noqa: B008
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Log analytics event."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only log your own events"
        )
    try:
        await service.log_event(
            user_id, event_type, event_data, content_id, client_event_id=client_event_id
        )
    except ValueError as exc:
        raise _invalid_metrics(exc)
    return {"status": "logged"}


@router.get("/user-events/{user_id}")
async def get_user_events(
    user_id: Annotated[UUID, Depends(require_self)],
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
    limit: int = Query(100, ge=1, le=MAX_EVENT_LIMIT),
):
    """Get user events."""
    events = await service.get_user_events(user_id, limit)
    return {"events": events, "total": len(events)}


@router.post("/view-events", response_model=dict)
async def record_view_event(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    content_id: UUID = Body(...),
    viewer_id: UUID = Body(...),
    watch_duration_seconds: int = Body(0),
    content_duration_seconds: int = Body(0),
    completion_pct: float = Body(0.0),
    playback_quality: str | None = Body(None),
    started_at: datetime | None = Body(None),  # noqa: B008
    completed_at: datetime | None = Body(None),  # noqa: B008
    client_event_id: str | None = Body(None),  # noqa: B008
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Record a content view/playback event."""
    if viewer_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only record your own views"
        )
    try:
        await service.record_view_event(
            content_id=content_id,
            viewer_id=viewer_id,
            watch_duration_seconds=watch_duration_seconds,
            content_duration_seconds=content_duration_seconds,
            completion_pct=completion_pct,
            playback_quality=playback_quality,
            started_at=started_at,
            completed_at=completed_at,
            client_event_id=client_event_id,
        )
    except ValueError as exc:
        raise _invalid_metrics(exc)
    return {"status": "recorded"}


@router.get("/creators/{creator_id}")
async def get_creator_analytics(
    creator_id: UUID,
    current_user: Annotated[UUID, Depends(require_self_or_admin)],
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Get analytics for a creator. Accessible to the creator themselves or
    to admins only.
    """
    analytics = await service.get_creator_analytics(creator_id)
    if not analytics:
        return {"creator_id": str(creator_id), "analytics": None}
    return analytics


@router.get("/content/{content_id}")
async def get_content_performance(
    content_id: UUID,
    current_user: Annotated[UUID, Depends(require_admin)],
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Get performance metrics for a content item. Admins only — content
    ownership cannot be established from analytics data alone, so ordinary
    users are not granted access.
    """
    performance = await service.get_content_performance(content_id)
    if not performance:
        return {"content_id": str(content_id), "metrics": None}
    return performance
