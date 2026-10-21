"""Recommendation service API routes."""

from typing import Annotated
from uuid import UUID

from jose import JWTError
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_auth import JWKSUnavailableError, verify_token_with_jwks

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import RecommendationRepository, UserPreferencesRepository
from app.services import RecommendationService

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


async def _decode_token(token: str) -> dict:
    """Verify an access token against auth-service's published JWKS.

    This dependency used to hand-roll a shared-secret HMAC decode.
    ``JWT_SECRET_KEY`` is a committed development value and ``DEV_ENVIRONMENTS``
    exempts it from the production validator, so a forged HS256 token carrying
    any ``sub`` was accepted (#941). The HS256 path is deleted rather than
    rotated -- rotating would keep every service on one symmetric key and reject
    genuine RS256 tokens.

    Token-type separation (#221) is no longer hand-checked: the shared verifier
    enforces ``expected_type="access"``, so a refresh token that shares the
    audience is refused here too (with the generic message, since the rejection
    now happens inside the verifier).
    """
    # JWKSUnavailableError must be caught *before* JWTError: it is a JWTError
    # subclass, and it is the branch that keeps a JWKS outage (fetch failure, or
    # a body that is not a JWKS) a 503 instead of a 401. Everything else the
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


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user id from the verified access-token sub."""
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
        return UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )


async def require_self(
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    request: Request,
) -> UUID:
    """Ensure the path user_id matches the authenticated user.

    Returns 404 (not 403) on mismatch so other users' existence is never
    disclosed (#435/#622).
    """
    path_user_id = request.path_params.get("user_id")
    if path_user_id is None or str(path_user_id) == str(jwt_user_id):
        return jwt_user_id
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Not found",
    )


async def get_rec_service(
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> RecommendationService:
    return RecommendationService(UserPreferencesRepository(db), RecommendationRepository(db))


@router.get("/for-user/{user_id}")
async def get_recommendations(
    user_id: Annotated[UUID, Depends(require_self)],
    request: Request,
    service: RecommendationService = Depends(get_rec_service),  # noqa: B008
    limit: Annotated[int, Query(ge=1, le=settings.MAX_RECOMMENDATION_LIMIT)] = 20,
):
    """Get personalized recommendations."""
    # Defense in depth: the resolved identity must equal the path user even
    # if the require_self dependency is bypassed (#435).
    path_user_id = request.path_params.get("user_id")
    if path_user_id is None or str(path_user_id) != str(user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    recommendations = await service.get_recommendations(user_id, limit)
    return {"recommendations": recommendations, "total": len(recommendations)}


def _validate_genre_list(name: str, genres: object) -> None:
    """Require bounded arrays of genre strings before persistence."""
    if genres is None:
        return
    if not isinstance(genres, list) or any(not isinstance(genre, str) for genre in genres):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} must be an array of strings",
        )
    if len(genres) > settings.MAX_PREFERENCE_GENRES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} exceeds the {settings.MAX_PREFERENCE_GENRES} genre limit",
        )


@router.put("/preferences/{user_id}")
async def update_preferences(
    user_id: Annotated[UUID, Depends(require_self)],
    service: RecommendationService = Depends(get_rec_service),  # noqa: B008
    body: dict | list | None = Body(None),  # noqa: B008
):
    """Update user preferences.

    Body may be a raw list of liked genre slugs (legacy) or an object with
    ``liked_genres`` / ``disliked_genres`` arrays (each bounded to
    ``MAX_PREFERENCE_GENRES``). Recommendations are regenerated afterwards.
    """
    liked_genres: list | None = None
    disliked_genres: list | None = None
    if isinstance(body, list):
        _validate_genre_list("liked_genres", body)
        liked_genres = body
    elif isinstance(body, dict):
        _validate_genre_list("liked_genres", body.get("liked_genres"))
        _validate_genre_list("disliked_genres", body.get("disliked_genres"))
        liked_genres = body.get("liked_genres")
        disliked_genres = body.get("disliked_genres")
    await service.update_preferences(user_id, liked_genres, disliked_genres)
    return {"status": "updated"}
