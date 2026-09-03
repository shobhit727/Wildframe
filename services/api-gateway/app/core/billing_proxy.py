"""Gateway billing proxy - jurisdiction detection from X-Jurisdiction plus rate limiting."""

import logging
from typing import Annotated

from fastapi import Header, Request

logger = logging.getLogger(__name__)


def detect_billing_jurisdiction(
    x_jurisdiction: Annotated[str | None, Header(alias="X-Jurisdiction")] = None,
    cf_ipcountry: Annotated[str | None, Header(alias="CF-IPCountry")] = None,
    request: Request | None = None,
) -> str:
    """Detect jurisdiction for billing - X-Jurisdiction header or GeoIP fallback."""
    if x_jurisdiction:
        return x_jurisdiction.upper()
    if cf_ipcountry:
        return cf_ipcountry.upper()
    # Fallback via request IP GeoIP would go here
    return "GLOBAL"


BILLING_RATE_LIMITS = {"EU": 100, "US": 200, "IN": 150, "GLOBAL": 100}


def billing_rate_limit(jurisdiction: str) -> int:
    """Return rate limit for jurisdiction."""
    return BILLING_RATE_LIMITS.get(jurisdiction.upper(), 100)
