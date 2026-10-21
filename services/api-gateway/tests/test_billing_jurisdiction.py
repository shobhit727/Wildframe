"""Jurisdiction normalisation in the billing proxy (#866).

``detect_billing_jurisdiction`` upper-cased the ``X-Jurisdiction`` /
``CF-IPCountry`` headers but never stripped them, while its sibling
``privacy_proxy.resolve_jurisdiction`` did. A padded header therefore survived
verbatim, missed the ``BILLING_RATE_LIMITS`` lookup, and silently received the
``GLOBAL`` default.

This is not hypothetical: header values reach an application with surrounding
whitespace intact. Sent over a real uvicorn + h11 socket, ``X-Jurisdiction:US``
(with no space after the colon) arrives as ``"US "`` -- h11 consumes the single
optional space that may follow the colon and nothing more -- so a client that
emits a trailing space loses its jurisdiction.

The failure direction is fail-safe (an unrecognised code gets ``GLOBAL``, the
most conservative entry in the table), which is exactly why it went unnoticed.
These tests pin the normalisation contract at the header boundary, where
untrusted input enters.
"""

import pytest

from app.core.billing_proxy import (
    BILLING_RATE_LIMITS,
    billing_rate_limit,
    detect_billing_jurisdiction,
)
from app.core.privacy_proxy import resolve_jurisdiction

# Every jurisdiction the rate-limit table actually prices differently, with the
# header spellings a real client/proxy can produce for it.
PADDED_HEADERS = [
    ("  US  ", "US"),
    ("US  ", "US"),
    ("  US", "US"),
    ("\tus\t", "US"),
    ("\tUS", "US"),
    ("  IN  ", "IN"),
    ("  eu  ", "EU"),
    ("  us-ca  ", "US-CA"),
]


@pytest.mark.parametrize(("header", "expected"), PADDED_HEADERS)
def test_a_padded_header_normalises_to_its_bare_code(header, expected):
    """The defect: padding survived, so the lookup could not match."""
    assert detect_billing_jurisdiction(header) == expected


@pytest.mark.parametrize(("header", "expected"), PADDED_HEADERS)
def test_a_padded_header_does_not_lose_its_rate_limit(header, expected):
    """A padded code must not silently drop to the GLOBAL default.

    This is the impact the issue describes: a legitimate US jurisdiction fell
    from 200 to 100 requests purely because of whitespace.
    """
    resolved = detect_billing_jurisdiction(header)
    assert billing_rate_limit(resolved) == BILLING_RATE_LIMITS.get(expected, 100)


@pytest.mark.parametrize("jurisdiction", sorted(BILLING_RATE_LIMITS))
def test_stripping_does_not_change_any_unpadded_jurisdiction(jurisdiction):
    """Normalising must be a no-op for input that was already clean."""
    assert detect_billing_jurisdiction(jurisdiction) == jurisdiction
    assert detect_billing_jurisdiction(jurisdiction.lower()) == jurisdiction


@pytest.mark.parametrize("cf_value", ["  us  ", "US ", " in"])
def test_the_geoip_fallback_header_is_normalised_too(cf_value):
    """Both headers in this function come off the wire and are trimmed alike.

    Normalising only X-Jurisdiction would leave the same bug on the GeoIP
    branch, which is the fallback that decides the jurisdiction when the
    explicit header is absent.
    """
    assert detect_billing_jurisdiction(None, cf_value) == cf_value.strip().upper()


@pytest.mark.parametrize("header", ["  US  ", "US", "us", "  eu  ", "ZZ", "  ZZ  ", ""])
def test_billing_normalisation_agrees_with_the_privacy_proxy(header):
    """The two jurisdiction resolvers must not disagree about the same input.

    ``resolve_jurisdiction`` strips before validating. This pins billing to the
    same answer for the codes both know about, so a future change to one seam
    cannot silently diverge from the other.
    """
    resolved = detect_billing_jurisdiction(header)
    privacy = resolve_jurisdiction(header)
    if privacy in BILLING_RATE_LIMITS:
        assert resolved == privacy


def test_unknown_and_blank_codes_still_fall_back_to_the_conservative_default():
    """Normalising must not widen the table or invent jurisdictions.

    Stripping is only whitespace removal: an unrecognised code still gets the
    GLOBAL default rather than being coerced onto a real jurisdiction, and a
    blank header still means "no signal".
    """
    assert billing_rate_limit("ZZ") == BILLING_RATE_LIMITS["GLOBAL"]
    assert billing_rate_limit("  ZZ  ") == BILLING_RATE_LIMITS["GLOBAL"]
    assert detect_billing_jurisdiction(None, None) == "GLOBAL"
    assert detect_billing_jurisdiction("", "") == "GLOBAL"
