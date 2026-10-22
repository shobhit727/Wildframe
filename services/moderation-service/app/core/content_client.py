"""HTTP client for resolving content ownership from content-service.

Strikes and automatic suspension hang off the creator who owns a piece of
content, so that identity must be resolved server-side from content-service —
never read from the flag request body. A reporter must not be able to aim a
strike (and a suspension) at a creator by supplying an arbitrary UUID.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx

from app.core.settings import settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def get_content_client() -> httpx.AsyncClient:
    """Process-wide bounded httpx client for content-service calls."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.CONTENT_SERVICE_URL,
            timeout=settings.CONTENT_SERVICE_TIMEOUT_SECONDS,
            limits=httpx.Limits(
                max_connections=settings.CONTENT_SERVICE_MAX_CONNECTIONS,
                max_keepalive_connections=settings.CONTENT_SERVICE_MAX_CONNECTIONS,
            ),
        )
    return _client


async def close_content_client() -> None:
    """Close the shared client (lifespan shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class ContentServiceUnavailableError(Exception):
    """content-service could not be reached; the creator must stay unresolved."""


async def resolve_content_owner(content_id: UUID) -> UUID | None:
    """Return the authoritative ``creator_id`` owning ``content_id``.

    Returns ``None`` when content-service reports the content does not exist.
    Raises :class:`ContentServiceUnavailableError` on transport or protocol
    errors — callers must fail the request closed, never fall back to a
    caller-supplied identity.
    """
    client = get_content_client()
    try:
        response = await client.get(f"/api/v1/content/{content_id}")
    except httpx.HTTPError as exc:
        logger.warning("content-service unavailable: %s", exc)
        raise ContentServiceUnavailableError(f"could not resolve content {content_id}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning(
            "content-service returned %s for content %s",
            response.status_code,
            content_id,
        )
        raise ContentServiceUnavailableError(f"could not resolve content {content_id}")
    try:
        payload = response.json()
        owner = payload.get("creator_id")
        return UUID(owner) if owner else None
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContentServiceUnavailableError(
            f"malformed content-service response for {content_id}"
        ) from exc
