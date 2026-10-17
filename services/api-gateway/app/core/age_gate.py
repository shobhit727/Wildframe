"""Gateway age gate - age-restricted route enforcement plus geographic proxy rules."""

import logging
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

logger = logging.getLogger(__name__)

# Routes that require age verification. These are whole path *segments*:
# "/maturity" covers "/maturity" and "/maturity/film", not "/maturityx".
AGE_RESTRICTED_PREFIXES = {"/maturity", "/purchase", "/content/restricted"}
# Jurisdictions with stricter minor age.
#
# KNOWN GAP (#1023) -- READ THIS BEFORE ADDING A DECISION ON TOP OF IT:
# this table does not influence any decision. `check_age_gate` reads it, puts
# the number in a log line, and returns a decision that does not contain it:
# a *verified minor* is admitted on every jurisdiction, at every listed age.
# There is no parental-consent enforcement and no jurisdiction-based denial.
# That is a missing policy, not a bug report; inventing one here would be
# inventing a legal position, so the two tests that used to "cover" the table
# (they asserted a constant was truthy) now assert this gap explicitly.
JURISDICTION_MINOR_AGE = {"EU": 16, "US": 13, "IN": 18, "GLOBAL": 16}

# Claim names. auth-service's POST /age/verify returns these in a response body
# (`jwt_claim`) and uses `minor_flag` as an alias for `is_minor`.
AGE_VERIFIED_CLAIM = "age_verified"
IS_MINOR_CLAIM = "is_minor"
MINOR_FLAG_CLAIM = "minor_flag"


def is_age_restricted(path: str) -> bool:
    """Check if path requires age verification.

    Matches on a path-segment boundary, not a raw substring: a prefix matches
    the path itself and anything beneath it. A bare ``startswith`` also matched
    unrelated siblings that merely shared a leading substring -- "/maturityx",
    "/purchase-order/list" (#879) -- and refused them. The over-match failed
    safe, but it was indistinguishable from a real gate hit, so it will 403 an
    unrelated route as soon as one of these prefixes gains a neighbour.
    """
    return any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in AGE_RESTRICTED_PREFIXES
    )


def _as_flag(value: object) -> str | None:
    """Coerce a claim value to the literal strings the header contract uses.

    Anything unrecognised becomes ``None`` -- "no claim" -- rather than a
    guess, because the only safe reading of an unknown value for a
    child-safety control is that the gate must still ask.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return "true"
    if text in ("false", "0", "no"):
        return "false"
    return None


def age_claim_from_token(claims: dict | None) -> tuple[str | None, str | None]:
    """Translate a *verified* token's age claims into the gate's header values.

    Returns ``(x_age_verified, x_is_minor)`` as literal ``"true"`` / ``"false"``
    strings, or ``None`` where the token says nothing about age.

    Absent is deliberately **not** normalised to ``"false"`` and never to
    ``"true"``: absent means unknown, and `check_age_gate` refuses anything
    that is not exactly ``"true"``, so unknown fails closed.

    Only a signature-verified token may be passed here. The gateway does not
    read ``X-Age-Verified`` / ``X-Is-Minor`` from the client at all -- those
    headers are stripped at the edge (see ``_STRIP_HEADERS``), because a
    client that can assert its own age is not gated at all.
    """
    if not claims:
        return None, None
    verified = _as_flag(claims.get(AGE_VERIFIED_CLAIM))
    minor = _as_flag(claims.get(IS_MINOR_CLAIM))
    if minor is None:
        minor = _as_flag(claims.get(MINOR_FLAG_CLAIM))
    return verified, minor


def check_age_gate(
    request: Request,
    x_age_verified: Annotated[str | None, Header(alias="X-Age-Verified")] = None,
    x_is_minor: Annotated[str | None, Header(alias="X-Is-Minor")] = None,
    x_jurisdiction: Annotated[str | None, Header(alias="X-Jurisdiction")] = None,
) -> dict | None:
    """Enforce age gate. Returns None if allowed, raises 403 if blocked.

    Called by `AgeGateMiddleware` (app/middleware.py), which is registered in
    `create_app` -- see test_age_gate_wiring.py, which drives the real ASGI app
    because this function was previously called from nowhere in production
    (#1023).

    `x_age_verified` is *not* read from the request. The middleware passes the
    values it derived from a verified bearer token; the header parameters are
    kept because they are the unit under test's input contract, not a second
    way in.
    """
    path = request.url.path
    if not is_age_restricted(path):
        return None
    # Require age_verified header for restricted routes
    if x_age_verified != "true":
        logger.warning(f"Age gate blocked {path} - not verified")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Age verification required for this content",
        )
    # Check minor + jurisdiction
    jurisdiction = (x_jurisdiction or "GLOBAL").upper()
    minor_age = JURISDICTION_MINOR_AGE.get(jurisdiction, 16)
    if x_is_minor == "true":
        # Log only. `minor_age` is interpolated here and then discarded -- see
        # the KNOWN GAP note on JURISDICTION_MINOR_AGE (#1023).
        logger.info(f"Minor access to {path} j={jurisdiction} minor_age={minor_age}")
        # Could require parental consent header here
    return {
        "age_verified": True,
        "is_minor": x_is_minor == "true",
        "jurisdiction": jurisdiction,
    }
