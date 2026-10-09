"""HTTP client for resolving content ownership from content-service.

Used by the analytics ownership checks: the content performance endpoint
must prove server-side that the requested ``content_id`` belongs to the
authenticated caller (or a privileged role) — the client-supplied value is
never trusted on its own.
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
    """content-service could not be reached; authorization fails closed."""


async def resolve_content_owner(content_id: UUID) -> UUID | None:
    """Return the authoritative ``creator_id`` for a piece of content.

    Returns ``None`` when content-service reports the content does not
    exist. Raises :class:`ContentServiceUnavailableError` on transport or
    protocol errors — callers must treat that as denial, never as allow.
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
        # Valid JSON is not necessarily an object. A 200 carrying a JSON scalar
        # ("...", 42, [], null) parses fine and then raises AttributeError on
        # .get(), which is not a ValueError/TypeError, so it escaped this block
        # and broke the fail-closed guarantee: require_content_access only
        # catches ContentServiceUnavailableError, so the request 500ed instead
        # of returning the documented 503 denial.
        #
        # Guarded explicitly rather than by adding AttributeError to the except
        # clause: blanket-catching AttributeError would also swallow a genuine
        # typo'd attribute access in this block and report it as a 503 denial,
        # turning a programming error into a silent fail-closed.
        if not isinstance(payload, dict):
            raise TypeError(f"expected a JSON object, got {type(payload).__name__}")
        owner = payload.get("creator_id")
        if not owner:
            return None
        # Same hazard one level down: UUID(42) raises AttributeError, not
        # ValueError, because the constructor calls str.replace on its argument.
        if not isinstance(owner, str):
            raise TypeError(f"creator_id must be a string, got {type(owner).__name__}")
        return UUID(owner)
    except (ValueError, TypeError) as exc:
        raise ContentServiceUnavailableError(
            f"malformed content-service response for {content_id}"
        ) from exc
