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
    """Detect jurisdiction for billing - X-Jurisdiction header or GeoIP fallback.

    Whitespace is stripped before upper-casing, matching
    ``privacy_proxy.resolve_jurisdiction``. Header values reach the application
    with surrounding whitespace intact -- h11 consumes only the single optional
    space after the colon -- so an un-stripped padded code missed the
    BILLING_RATE_LIMITS lookup and silently took the GLOBAL default (#866).

    Only whitespace is removed. The code is not coerced onto a known
    jurisdiction: an unrecognised value still falls through to the conservative
    GLOBAL limit at lookup time, so this cannot widen the table.
    """
    if x_jurisdiction:
        return x_jurisdiction.strip().upper()
    if cf_ipcountry:
        return cf_ipcountry.strip().upper()
    # Fallback via request IP GeoIP would go here
    return "GLOBAL"


BILLING_RATE_LIMITS = {"EU": 100, "US": 200, "IN": 150, "GLOBAL": 100}


def billing_rate_limit(jurisdiction: str) -> int:
    """Return rate limit for jurisdiction."""
    return BILLING_RATE_LIMITS.get(jurisdiction.strip().upper(), 100)
