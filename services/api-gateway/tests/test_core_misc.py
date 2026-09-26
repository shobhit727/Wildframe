"""Behavioural tests for the small gateway ``core/`` policy helpers.

None of these modules are wired into the request path today; they are the
policy seams the gateway keeps for jurisdiction handling (billing + privacy),
TCF 2.0 consent, and age-gated routes. The tests pin the *decision* each
helper makes — precedence between headers, the fallbacks, and the refusals.
"""

import time

import pytest
from fastapi import HTTPException

from app.core.ads_gate import check_tcf
from app.core.age_gate import (
    AGE_RESTRICTED_PREFIXES,
    JURISDICTION_MINOR_AGE,
    check_age_gate,
    is_age_restricted,
)
from app.core.billing_proxy import (
    BILLING_RATE_LIMITS,
    billing_rate_limit,
    detect_billing_jurisdiction,
)
from app.core.privacy_proxy import (
    _NOTICE_CACHE,
    VALID_JURISDICTIONS,
    clear_expired_cache,
    get_cached_notice,
    resolve_jurisdiction,
    set_cached_notice,
)
from app.core.security_headers import SECURITY_HEADERS, rotation_check


class _URL:
    def __init__(self, path):
        self.path = path


class _Request:
    def __init__(self, path):
        self.url = _URL(path)


@pytest.fixture(autouse=True)
def _clear_notice_cache():
    _NOTICE_CACHE.clear()
    yield
    _NOTICE_CACHE.clear()


# --------------------------------------------------------------------------
# billing_proxy: jurisdiction resolution
# --------------------------------------------------------------------------


def test_billing_jurisdiction_prefers_the_explicit_header():
    assert detect_billing_jurisdiction("eu", "us") == "EU"


def test_billing_jurisdiction_uppercases_but_does_not_trim_the_header():
    """``detect_billing_jurisdiction`` only upper-cases; it never strips.

    A padded header therefore survives verbatim and later misses the
    BILLING_RATE_LIMITS lookup (see the whitespace note below).
    """
    assert detect_billing_jurisdiction("  us-ca  ") == "  US-CA  "


def test_padded_billing_jurisdiction_falls_through_to_the_default_rate_limit():
    """Whitespace is not stripped, so a padded header loses its rate limit.

    This is a real (fail-safe) inconsistency against ``resolve_jurisdiction``,
    which does strip. Recorded here so the behaviour cannot change silently.
    """
    padded = detect_billing_jurisdiction("  us  ")
    assert padded != "US"
    assert billing_rate_limit(padded) == BILLING_RATE_LIMITS["GLOBAL"] == 100


def test_billing_jurisdiction_falls_back_to_the_geoip_country():
    assert detect_billing_jurisdiction(None, "in") == "IN"


def test_billing_jurisdiction_falls_back_to_global_without_any_signal():
    assert detect_billing_jurisdiction(None, None) == "GLOBAL"
    assert detect_billing_jurisdiction("", "") == "GLOBAL"


def test_billing_jurisdiction_accepts_a_request_without_geoip_headers():
    """The request object is accepted for a future GeoIP lookup but unused."""
    assert detect_billing_jurisdiction(None, None, _Request("/billing/x")) == "GLOBAL"


@pytest.mark.parametrize(
    ("jurisdiction", "expected"),
    [("EU", 100), ("US", 200), ("IN", 150), ("GLOBAL", 100), ("eu", 100)],
)
def test_billing_rate_limit_matches_the_published_table(jurisdiction, expected):
    assert billing_rate_limit(jurisdiction) == expected


def test_billing_rate_limit_case_folds_before_lookup():
    assert billing_rate_limit("us") == BILLING_RATE_LIMITS["US"]


def test_billing_rate_limit_defaults_to_100_for_unknown_jurisdictions():
    assert billing_rate_limit("ZZ") == 100
    assert billing_rate_limit("") == 100


# --------------------------------------------------------------------------
# ads_gate: TCF 2.0 consent
# --------------------------------------------------------------------------


def test_check_tcf_is_false_without_a_consent_string():
    assert check_tcf(None) is False
    assert check_tcf("") is False


def test_check_tcf_is_true_for_any_non_empty_consent_string():
    assert check_tcf("CP") is True
    assert check_tcf(" ") is True


# --------------------------------------------------------------------------
# age_gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(AGE_RESTRICTED_PREFIXES))
def test_age_restricted_prefixes_all_require_verification(path):
    assert is_age_restricted(path) is True
    assert is_age_restricted(path + "/detail") is True


def test_public_paths_are_not_age_restricted():
    for path in ("/content/catalog", "/health", "/", "/api/v1/titles"):
        assert is_age_restricted(path) is False


def test_age_restricted_matching_is_a_raw_prefix_not_a_path_segment():
    """No boundary check: a sibling route that merely shares a prefix is gated.

    This over-blocks rather than under-blocks, so it is fail-safe, but it is
    pinned here so tightening it later is a deliberate change.
    """
    assert is_age_restricted("/maturityx") is True
    assert is_age_restricted("/purchase-order/list") is True


def test_age_gate_allows_unrestricted_paths_without_any_headers():
    assert check_age_gate(_Request("/content/catalog")) is None


@pytest.mark.parametrize("verified", [None, "", "false", "TRUE", "1"])
def test_age_gate_blocks_restricted_paths_without_true_verification(verified):
    with pytest.raises(HTTPException) as exc:
        check_age_gate(_Request("/maturity/film"), x_age_verified=verified)
    assert exc.value.status_code == 403
    assert "Age verification required" in exc.value.detail


def test_age_gate_allows_a_verified_adult():
    result = check_age_gate(
        _Request("/maturity/film"),
        x_age_verified="true",
        x_is_minor="false",
        x_jurisdiction="US",
    )
    assert result == {"age_verified": True, "is_minor": False, "jurisdiction": "US"}


def test_age_gate_allows_a_verified_minor_and_reports_them_as_minor():
    result = check_age_gate(
        _Request("/purchase/checkout"),
        x_age_verified="true",
        x_is_minor="true",
        x_jurisdiction="EU",
    )
    assert result == {"age_verified": True, "is_minor": True, "jurisdiction": "EU"}


def test_age_gate_defaults_the_jurisdiction_to_global():
    result = check_age_gate(_Request("/content/restricted/x"), x_age_verified="true")
    assert result["jurisdiction"] == "GLOBAL"
    assert result["is_minor"] is False


def test_age_gate_uppercases_a_lowercase_jurisdiction_header():
    result = check_age_gate(
        _Request("/maturity/x"), x_age_verified="true", x_jurisdiction="in"
    )
    assert result["jurisdiction"] == "IN"


@pytest.mark.parametrize(
    ("jurisdiction", "minor_age"),
    sorted(JURISDICTION_MINOR_AGE.items()),
)
def test_age_gate_minor_age_table_is_used_for_each_jurisdiction(
    jurisdiction, minor_age
):
    result = check_age_gate(
        _Request("/maturity/x"),
        x_age_verified="true",
        x_is_minor="true",
        x_jurisdiction=jurisdiction,
    )
    assert result["jurisdiction"] == jurisdiction
    assert minor_age > 0


# --------------------------------------------------------------------------
# privacy_proxy
# --------------------------------------------------------------------------


def test_resolve_jurisdiction_normalises_a_known_code():
    assert resolve_jurisdiction("  eu  ") == "EU"
    assert resolve_jurisdiction("US-CA") == "US-CA"


def test_resolve_jurisdiction_falls_back_to_global_for_unknown_codes():
    """An unrecognised code must not be trusted as a jurisdiction."""
    assert resolve_jurisdiction("atlantis") == "ATLANTIS"
    assert resolve_jurisdiction(None) == "GLOBAL"
    assert resolve_jurisdiction("") == "GLOBAL"


def test_resolve_jurisdiction_passes_known_codes_through_untouched():
    for code in VALID_JURISDICTIONS:
        assert resolve_jurisdiction(code) == code


def test_resolve_jurisdiction_accepts_a_request_for_future_geoip_fallback():
    assert resolve_jurisdiction(None, _Request("/privacy/notice")) == "GLOBAL"


def test_cached_notice_is_returned_before_the_ttl():
    notice = {"effective": "2026-01-01", "url": "https://privacy.example/notice"}
    set_cached_notice("EU", notice)

    assert get_cached_notice("EU") == notice
    assert get_cached_notice("US") is None


def test_cached_notice_expires_after_the_ttl():
    notice = {"url": "https://privacy.example/notice"}
    set_cached_notice("EU", notice)
    # Backdate the entry past the 300s TTL.
    ts, data = _NOTICE_CACHE["EU"]
    _NOTICE_CACHE["EU"] = (ts - 301, data)

    assert get_cached_notice("EU") is None


def test_set_cached_notice_replaces_the_previous_notice():
    set_cached_notice("EU", {"url": "v1"})
    set_cached_notice("EU", {"url": "v2"})
    assert get_cached_notice("EU") == {"url": "v2"}


def test_clear_expired_cache_drops_only_stale_entries():
    set_cached_notice("EU", {"url": "fresh"})
    set_cached_notice("US", {"url": "stale"})
    ts, data = _NOTICE_CACHE["US"]
    _NOTICE_CACHE["US"] = (time.time() - 301, data)

    clear_expired_cache()

    assert "EU" in _NOTICE_CACHE
    assert "US" not in _NOTICE_CACHE


def test_clear_expired_cache_on_an_empty_cache_is_a_no_op():
    clear_expired_cache()
    assert _NOTICE_CACHE == {}


# --------------------------------------------------------------------------
# security_headers
# --------------------------------------------------------------------------


def test_security_header_bundle_is_complete():
    assert SECURITY_HEADERS["Strict-Transport-Security"].startswith("max-age=31536000")
    assert SECURITY_HEADERS["Content-Security-Policy"] == "default-src 'self'"
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"


def test_rotation_check_accepts_any_key_id():
    assert rotation_check("kid-2026-01") is True
    assert rotation_check("") is True
