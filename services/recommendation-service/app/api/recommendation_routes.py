"""Recommendation service API routes."""

from typing import Annotated
from uuid import UUID

from jose import JWTError, jwt
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import RecommendationRepository, UserPreferencesRepository
from app.services import RecommendationService

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


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
    except JWTError:
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


async def get_rec_service(
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> RecommendationService:
    return RecommendationService(UserPreferencesRepository(db), RecommendationRepository(db))


@router.get("/for-user/{user_id}")
async def get_recommendations(
    user_id: Annotated[UUID, Depends(require_self)],
    service: RecommendationService = Depends(get_rec_service),  # noqa: B008
    limit: int = 20,
):
    """Get personalized recommendations."""
    recommendations = await service.get_recommendations(user_id, limit)
    return {"recommendations": recommendations, "total": len(recommendations)}


@router.put("/preferences/{user_id}")
async def update_preferences(
    user_id: Annotated[UUID, Depends(require_self)],
    service: RecommendationService = Depends(get_rec_service),  # noqa: B008
    body: dict | list | None = Body(None),  # noqa: B008
):
    """Update user preferences.

    Body may be a raw list of liked genre slugs (legacy) or an object with
    ``liked_genres`` / ``disliked_genres`` arrays. Recommendations are
    regenerated afterwards.
    """
    if isinstance(body, list):
        liked_genres = body or None
        disliked_genres = None
    elif isinstance(body, dict):
        liked_genres = body.get("liked_genres")
        disliked_genres = body.get("disliked_genres")
    else:
        liked_genres = disliked_genres = None
    await service.update_preferences(user_id, liked_genres, disliked_genres)
    return {"status": "updated"}
