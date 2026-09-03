"""Tests for privacy proxy."""

from app.core.privacy_proxy import resolve_jurisdiction, VALID_JURISDICTIONS


def test_resolve_jurisdiction_header():
    assert resolve_jurisdiction("EU") == "EU"


def test_valid_jurisdictions():
    assert "EU" in VALID_JURISDICTIONS
