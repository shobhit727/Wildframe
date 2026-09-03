"""Age middleware for Gateway - injects age headers from JWT claims."""

import logging

from fastapi import Request, Response

logger = logging.getLogger(__name__)


async def age_middleware(request: Request, call_next):
    """Middleware that propagates age claims from JWT to upstream headers."""
    # In prod, decode JWT and inject X-Age-Verified, X-Is-Minor
    # For now, pass through and let age_gate enforce
    response = await call_next(request)
    # Add Vary to help caches
    response.headers["Vary"] = "X-Jurisdiction, X-Age-Verified"
    return response
