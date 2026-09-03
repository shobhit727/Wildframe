"""Tests for age gate."""

from app.core.age_gate import is_age_restricted, JURISDICTION_MINOR_AGE


def test_age_restricted():
    assert is_age_restricted("/maturity/check") is True
    assert is_age_restricted("/health") is False


def test_minor_age():
    assert JURISDICTION_MINOR_AGE["EU"] == 16
