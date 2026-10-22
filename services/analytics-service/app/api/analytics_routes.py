"""Analytics service API routes."""

from typing import Annotated
from uuid import UUID

from jose import JWTError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_auth import JWKSUnavailableError, verify_token_with_jwks

from app.core.content_client import (
    ContentServiceUnavailableError,
    resolve_content_owner,
)
from app.core.database import get_db
from app.core.settings import settings
from app.repositories import (
    ContentPerformanceMetricsRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
    EventRepository,
)
from app.schemas import LogEventRequest, RecordViewEventRequest
from app.services import MAX_EVENT_LIMIT, AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


async def _decode_token(token: str) -> dict:
    """Verify an access token against auth-service's published JWKS.

    This boundary used to hand-roll its own symmetric HMAC decode, making
    analytics-service a shared-secret verifier (#941): ``JWT_SECRET_KEY`` is a
    committed development value and ``DEV_ENVIRONMENTS`` exempts it from the
    production validator, so a forged HS256 token carrying any ``sub`` and
    ``role: "admin"`` authenticated. The HS256 path is deleted rather than
    rotated -- rotating would keep the service on one symmetric key and reject
    genuine RS256 tokens.

    Token-type separation (#221) is delegated to the shared verifier via
    ``expected_type="access"``: a refresh token shares the audience but is not
    accepted here.
    """
    # JWKSUnavailableError must be caught *before* JWTError: it is a JWTError
    # subclass, and it is the branch that keeps a JWKS outage (fetch failure,
    # or a body that is not a JWKS) a 503 instead of a 401. Everything else the
    # verifier rejects -- bad signature, expired, wrong audience, unknown kid --
    # stays a 401.
    try:
        return await verify_token_with_jwks(
            token,
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            url=settings.JWT_JWKS_URL,
            expected_type="access",
        )
    except JWKSUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token verification is unavailable",
        ) from exc
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc


async def get_current_user_claims(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict:
    """Resolve the authenticated identity and role from the JWT.

    Returns ``{"user_id": UUID, "role": str}``. The role claim comes from
    the auth-service token (``admin`` via its allow-list); it is used to
    grant privileged access to creator/content analytics.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    payload = await _decode_token(authorization.removeprefix("Bearer "))
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
    return {"user_id": user_id, "role": payload.get("role", "user")}


async def get_current_user_id(
    claims: Annotated[dict, Depends(get_current_user_claims)],
) -> UUID:
    """Resolve the authenticated user id from the JWT sub claim."""
    user_id = claims["user_id"]
    assert isinstance(user_id, UUID)
    return user_id


async def require_self(
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    request: Request,
) -> UUID:
    """Ensure the path user_id matches the authenticated user."""
    path_user_id = request.path_params.get("user_id")
    if path_user_id is None or str(path_user_id) == str(jwt_user_id):
        return jwt_user_id
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Not found",
    )


async def require_creator_access(
    claims: Annotated[dict, Depends(get_current_user_claims)],
    request: Request,
) -> UUID:
    """Creator analytics are private: creator-self or a privileged role.

    A creator may always read their own analytics; ``PRIVILEGED_ROLE``
    (admin) may read any creator's. Everyone else gets 404 — ordinary
    users never inherit creator-scope access.
    """
    creator_id = request.path_params.get("creator_id")
    if creator_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if str(creator_id) == str(claims["user_id"]) or claims["role"] == settings.PRIVILEGED_ROLE:
        return UUID(str(creator_id))
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Not found",
    )


async def require_content_access(
    claims: Annotated[dict, Depends(get_current_user_claims)],
    request: Request,
) -> UUID:
    """Content performance metrics are private to the owning creator.

    The ownership is resolved server-side from content-service: a
    client-supplied ``creator_id`` is never trusted. Privileged roles may
    read any content; a creator may read only content they own; everyone
    else gets 404. Fail-closed: if ownership cannot be resolved (content
    missing or service unreachable) the request is denied.
    """
    content_id = request.path_params.get("content_id")
    if content_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        content_id = UUID(str(content_id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid content_id",
        ) from None
    if claims["role"] == settings.PRIVILEGED_ROLE:
        return content_id
    try:
        owner = await resolve_content_owner(content_id)
    except ContentServiceUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify content ownership",
        ) from None
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    if owner != claims["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    return content_id


async def get_analytics_service(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AnalyticsService:
    # The lifespan-managed redis client backs client_event_id idempotency;
    # when it is absent the service logs and proceeds without dedup.
    return AnalyticsService(
        EventRepository(db),
        ContentViewEventRepository(db),
        CreatorAnalyticsSnapshotRepository(db),
        ContentPerformanceMetricsRepository(db),
        dedup_store=getattr(request.app.state, "redis_client", None),
    )


@router.post("/events", response_model=dict)
async def log_event(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    request: LogEventRequest,
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Log analytics event."""
    if request.user_id != current_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        await service.log_event(
            request.user_id,
            request.event_type,
            request.event_data,
            request.content_id,
            request.client_event_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"status": "logged"}


@router.get("/user-events/{user_id}")
async def get_user_events(
    user_id: Annotated[UUID, Depends(require_self)],
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
    limit: int = Query(default=100, ge=1, le=MAX_EVENT_LIMIT),
):
    """Get user events."""
    events = await service.get_user_events(user_id, limit)
    return {"events": events, "total": len(events)}


@router.post("/view-events", response_model=dict)
async def record_view_event(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    request: RecordViewEventRequest,
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Record a content view/playback event."""
    if request.viewer_id != current_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        await service.record_view_event(
            content_id=request.content_id,
            viewer_id=request.viewer_id,
            watch_duration_seconds=request.watch_duration_seconds,
            content_duration_seconds=request.content_duration_seconds,
            completion_pct=request.completion_pct,
            playback_quality=request.playback_quality,
            started_at=request.started_at,
            completed_at=request.completed_at,
            client_event_id=request.client_event_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"status": "recorded"}


@router.get("/creators/{creator_id}")
async def get_creator_analytics(
    creator_id: UUID,
    _access: Annotated[UUID, Depends(require_creator_access)],
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Get analytics for a creator.

    Access is creator-self or ``PRIVILEGED_ROLE`` only; other
    authenticated users receive 404. These metrics are private.
    """
    analytics = await service.get_creator_analytics(creator_id)
    if not analytics:
        return {"creator_id": str(creator_id), "analytics": None}
    return analytics


@router.get("/content/{content_id}")
async def get_content_performance(
    content_id: UUID,
    _access: Annotated[UUID, Depends(require_content_access)],
    service: AnalyticsService = Depends(get_analytics_service),  # noqa: B008
):
    """Get performance metrics for content.

    Access is granted only to the owning creator (verified server-side
    via content-service) or a privileged role. Fail-closed on ownership
    resolution errors.
    """
    performance = await service.get_content_performance(content_id)
    if not performance:
        return {"content_id": str(content_id), "metrics": None}
    return performance
