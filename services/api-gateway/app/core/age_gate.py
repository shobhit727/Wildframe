"""Gateway age gate - age-restricted route enforcement plus geographic proxy rules."""

import logging
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from app.core.privacy_proxy import VALID_JURISDICTIONS

logger = logging.getLogger(__name__)

# Routes that require age verification
AGE_RESTRICTED_PREFIXES = {"/maturity", "/purchase", "/content/restricted"}
# Jurisdictions with stricter minor age
JURISDICTION_MINOR_AGE = {"EU": 16, "US": 13, "IN": 18, "GLOBAL": 16}


def is_age_restricted(path: str) -> bool:
    """Check if path requires age verification."""
    return any(path.startswith(prefix) for prefix in AGE_RESTRICTED_PREFIXES)


def check_age_gate(
    request: Request,
    x_age_verified: Annotated[str | None, Header(alias="X-Age-Verified")] = None,
    x_is_minor: Annotated[str | None, Header(alias="X-Is-Minor")] = None,
    x_jurisdiction: Annotated[str | None, Header(alias="X-Jurisdiction")] = None,
) -> dict | None:
    """Enforce age gate - called from proxy middleware. Returns None if allowed, raises 403 if blocked."""
    path = request.url.path
    if not is_age_restricted(path):
        return None
    # Require age_verified header for restricted routes
    if x_age_verified != "true":
        logger.warning(f"Age gate blocked {path} - not verified")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Age verification required for this content")
    # Check minor + jurisdiction
    jurisdiction = (x_jurisdiction or "GLOBAL").upper()
    minor_age = JURISDICTION_MINOR_AGE.get(jurisdiction, 16)
    if x_is_minor == "true":
        logger.info(f"Minor access to {path} j={jurisdiction} minor_age={minor_age}")
        # Could require parental consent header here
    return {"age_verified": True, "is_minor": x_is_minor == "true", "jurisdiction": jurisdiction}
