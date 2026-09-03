"""Tests for security headers."""

from app.core.security_headers import SECURITY_HEADERS, rotation_check


def test_security_headers():
    assert "Strict-Transport-Security" in SECURITY_HEADERS


def test_rotation():
    assert rotation_check("k1") is True
