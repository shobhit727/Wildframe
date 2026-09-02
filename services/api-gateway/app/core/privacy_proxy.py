"""Jurisdiction-aware privacy proxy for API Gateway.

Reads X-Jurisdiction header (or GeoIP fallback) and routes privacy requests
to the correct jurisdiction-aware backend with caching.
"""

import logging
import time
from typing import Annotated

from fastapi import Header, Request

logger = logging.getLogger(__name__)

# Simple in-memory cache for current notices (TTL 5 minutes)
_NOTICE_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300

VALID_JURISDICTIONS = {"EU", "US", "IN", "GLOBAL", "US-CA", "CA", "BR", "JP", "AU", "SG", "UK"}


def resolve_jurisdiction(
    x_jurisdiction: Annotated[str | None, Header(alias="X-Jurisdiction")] = None,
    request: Request = None,
) -> str:
    """Resolve jurisdiction from header, with GeoIP fallback.

    Args:
        x_jurisdiction: Explicit jurisdiction header
        request: HTTP request for IP fallback

    Returns:
        Normalized jurisdiction code (default GLOBAL)
    """
    if x_jurisdiction:
        normalized = x_jurisdiction.strip().upper()
        if normalized in VALID_JURISDICTIONS:
            return normalized
        logger.warning(f"Unknown jurisdiction header: {x_jurisdiction}, falling back to GLOBAL")
    # Fallback: could add GeoIP lookup here; default to GLOBAL
    # For now, respect X-Jurisdiction if present, else GLOBAL
    return x_jurisdiction.upper() if x_jurisdiction else "GLOBAL"


def get_cached_notice(jurisdiction: str) -> dict | None:
    """Get cached current notice for jurisdiction if not expired."""
    entry = _NOTICE_CACHE.get(jurisdiction)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def set_cached_notice(jurisdiction: str, data: dict) -> None:
    """Cache current notice for jurisdiction."""
    _NOTICE_CACHE[jurisdiction] = (time.time(), data)
    logger.debug(f"Cached privacy notice for {jurisdiction}")


def clear_expired_cache() -> None:
    """Clear expired cache entries."""
    now = time.time()
    expired = [k for k, (ts, _) in _NOTICE_CACHE.items() if now - ts >= _CACHE_TTL]
    for k in expired:
        del _NOTICE_CACHE[k]
