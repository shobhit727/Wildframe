"""Behavioural tests for ``app.core.money`` — exact Decimal money arithmetic.

The billing service never uses binary floating point for currency: provider
boundaries speak integer minor units and the domain speaks exact
:class:`decimal.Decimal`. These tests pin the ISO-4217 allowlist behaviour,
the minor-unit conversions, and — most importantly — that precision is
*never* silently truncated on the way to a provider.
"""

from decimal import Decimal

import pytest

from app.core.money import (
    CURRENCY_MINOR_UNITS,
    DEFAULT_CURRENCY,
    CurrencyError,
    from_minor_units,
    minor_units,
    to_minor_units,
    validate_currency,
)


# ---------------------------------------------------------------------------
# The allowlist itself
# ---------------------------------------------------------------------------


class TestAllowlistIntegrity:
    def test_default_currency_is_supported(self):
        assert DEFAULT_CURRENCY in CURRENCY_MINOR_UNITS

    def test_every_exponent_is_within_iso4217_bounds(self):
        # ISO-4217 defines 0, 2 and 3 decimal places; nothing else exists.
        assert set(CURRENCY_MINOR_UNITS.values()) == {0, 2, 3}

    def test_every_code_is_three_uppercase_letters(self):
        for code, units in CURRENCY_MINOR_UNITS.items():
            assert len(code) == 3, code
            assert code.isalpha(), code
            assert code.isupper(), code
            assert units in (0, 2, 3), code

    @pytest.mark.parametrize(
        "code,expected_units",
        [
            ("USD", 2),
            ("EUR", 2),
            ("GBP", 2),
            ("INR", 2),
            # 0-decimal currencies must not be silently given 2 places.
            ("JPY", 0),
            ("KRW", 0),
            ("ISK", 0),
            ("VND", 0),
            # 3-decimal currencies must not be silently given 2 places.
            ("BHD", 3),
            ("KWD", 3),
            ("JOD", 3),
            ("OMR", 3),
        ],
    )
    def test_minor_units_exponents(self, code, expected_units):
        assert CURRENCY_MINOR_UNITS[code] == expected_units
        assert minor_units(code) == expected_units


# ---------------------------------------------------------------------------
# validate_currency
# ---------------------------------------------------------------------------


class TestValidateCurrency:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("USD", "USD"),
            ("usd", "USD"),
            ("uSd", "USD"),
            ("  eur  ", "EUR"),
            ("jpy", "JPY"),
        ],
    )
    def test_normalizes_to_uppercase_and_trims(self, raw, expected):
        assert validate_currency(raw) == expected

    @pytest.mark.parametrize("empty", ["", None])
    def test_empty_code_is_rejected(self, empty):
        with pytest.raises(CurrencyError, match="must not be empty"):
            validate_currency(empty)

    @pytest.mark.parametrize(
        "bad",
        [
            "US",  # too short
            "USDD",  # too long
            "US1",  # not alphabetic
            "12",  # digits only
            "US ",  # 2 letters after strip
            "U$D",
        ],
    )
    def test_malformed_code_is_rejected(self, bad):
        with pytest.raises(CurrencyError, match="expected 3 letters"):
            validate_currency(bad)

    @pytest.mark.parametrize("unknown", ["ZZZ", "ABC", "XBT", "USX"])
    def test_well_formed_but_unlisted_code_is_rejected(self, unknown):
        with pytest.raises(CurrencyError, match="Unsupported currency code"):
            validate_currency(unknown)

    def test_currency_error_is_a_value_error(self):
        # Callers catch ValueError; the hierarchy must preserve that.
        assert issubclass(CurrencyError, ValueError)

    def test_reported_code_in_error_is_normalized(self):
        with pytest.raises(CurrencyError) as exc:
            validate_currency("zzz")
        assert "'ZZZ'" in str(exc.value)


# ---------------------------------------------------------------------------
# minor_units
# ---------------------------------------------------------------------------


class TestMinorUnits:
    @pytest.mark.parametrize(
        "code,expected",
        [("USD", 2), ("JPY", 0), ("BHD", 3), ("nzd", 2), ("kwd", 3)],
    )
    def test_returns_exponent(self, code, expected):
        assert minor_units(code) == expected

    def test_rejects_unknown_currency(self):
        with pytest.raises(CurrencyError):
            minor_units("ZZZ")

    def test_rejects_empty_currency(self):
        with pytest.raises(CurrencyError, match="must not be empty"):
            minor_units("")


# ---------------------------------------------------------------------------
# to_minor_units
# ---------------------------------------------------------------------------


class TestToMinorUnits:
    @pytest.mark.parametrize(
        "amount,currency,expected",
        [
            # 2-decimal currencies
            (Decimal("0.00"), "USD", 0),
            (Decimal("0.01"), "USD", 1),
            (Decimal("1.00"), "USD", 100),
            (Decimal("7.99"), "USD", 799),
            (Decimal("-1.50"), "USD", -150),
            (Decimal("1234.56"), "EUR", 123456),
            (Decimal("100"), "GBP", 10000),
            # 0-decimal currencies: the amount must already be integral
            (Decimal("1000"), "JPY", 1000),
            (Decimal("0"), "JPY", 0),
            (Decimal("7"), "KRW", 7),
            (Decimal("-500"), "VND", -500),
            # 3-decimal currencies
            (Decimal("1.234"), "BHD", 1234),
            (Decimal("0.001"), "BHD", 1),
            (Decimal("10.000"), "KWD", 10000),
            (Decimal("-0.007"), "OMR", -7),
        ],
    )
    def test_exact_conversions(self, amount, currency, expected):
        result = to_minor_units(amount, currency)
        assert result == expected
        assert isinstance(result, int)

    @pytest.mark.parametrize(
        "amount,currency",
        [
            # More precision than USD/EUR allow — must raise, never truncate.
            (Decimal("1.234"), "USD"),
            (Decimal("0.001"), "EUR"),
            (Decimal("10.999"), "GBP"),
            # More precision than a 0-decimal currency allows.
            (Decimal("1.5"), "JPY"),
            (Decimal("0.01"), "KRW"),
            (Decimal("100.001"), "ISK"),
            # More precision than a 3-decimal currency allows.
            (Decimal("1.2345"), "BHD"),
            (Decimal("0.0001"), "KWD"),
        ],
    )
    def test_excess_precision_is_rejected_not_truncated(self, amount, currency):
        with pytest.raises(CurrencyError, match="decimal places"):
            to_minor_units(amount, currency)

    def test_excess_precision_error_names_amount_currency_and_exponent(self):
        with pytest.raises(CurrencyError) as exc:
            to_minor_units(Decimal("1.005"), "USD")
        message = str(exc.value)
        assert "1.005" in message
        assert "USD" in message
        assert "2 decimal places" in message

    def test_excess_precision_error_for_zero_decimal_currency(self):
        with pytest.raises(CurrencyError) as exc:
            to_minor_units(Decimal("5.5"), "JPY")
        assert "0 decimal places" in str(exc.value)

    def test_exact_amount_at_the_precision_boundary_is_accepted(self):
        # 2 dp in USD is legal; 3 dp is not.
        assert to_minor_units(Decimal("0.07"), "USD") == 7
        assert to_minor_units(Decimal("0.007"), "BHD") == 7

    def test_trailing_zeros_do_not_count_as_excess_precision(self):
        # 1.10 == 1.1 exactly at 2 dp; the trailing zero must not trip the guard.
        assert to_minor_units(Decimal("1.10"), "USD") == 110
        assert to_minor_units(Decimal("1.000"), "BHD") == 1000
        assert to_minor_units(Decimal("3.00"), "JPY") == 3

    def test_high_precision_decimal_scale_does_not_trip_the_guard(self):
        # Decimal carries scale; only *value* precision matters.
        assert to_minor_units(Decimal("5.00"), "USD") == 500
        assert to_minor_units(Decimal("7.9900"), "USD") == 799

    def test_very_large_amount_converts_without_precision_loss(self):
        # Stripe's ceiling is far below this; we still must not lose digits.
        big = Decimal("99999999999999.99")
        assert to_minor_units(big, "USD") == 9999999999999999

    def test_rejects_unknown_currency_before_looking_at_amount(self):
        with pytest.raises(CurrencyError, match="Unsupported currency code"):
            to_minor_units(Decimal("1.00"), "ZZZ")

    def test_rejects_empty_currency(self):
        with pytest.raises(CurrencyError, match="must not be empty"):
            to_minor_units(Decimal("1.00"), "")

    def test_currency_code_is_case_insensitive(self):
        assert to_minor_units(Decimal("2.50"), "usd") == 250
        assert to_minor_units(Decimal("2"), "jpy") == 2
        assert to_minor_units(Decimal("2.500"), "bhd") == 2500


# ---------------------------------------------------------------------------
# from_minor_units
# ---------------------------------------------------------------------------


class TestFromMinorUnits:
    @pytest.mark.parametrize(
        "minor,currency,expected",
        [
            (0, "USD", Decimal("0")),
            (1, "USD", Decimal("0.01")),
            (99, "USD", Decimal("0.99")),
            (100, "USD", Decimal("1.00")),
            (799, "USD", Decimal("7.99")),
            (123456, "EUR", Decimal("1234.56")),
            (-150, "USD", Decimal("-1.50")),
            (0, "JPY", Decimal("0")),
            (1000, "JPY", Decimal("1000")),
            (7, "KRW", Decimal("7")),
            (1, "BHD", Decimal("0.001")),
            (1234, "BHD", Decimal("1.234")),
            (-7, "OMR", Decimal("-0.007")),
        ],
    )
    def test_exact_conversions(self, minor, currency, expected):
        result = from_minor_units(minor, currency)
        assert result == expected
        assert isinstance(result, Decimal)

    def test_never_raises_on_large_minor_values(self):
        # from_minor_units is the untrusted-input direction: Stripe gives us an
        # int, and rounding must not be involved in reading it back.
        result = from_minor_units(10**18, "USD")
        assert result == Decimal(10**18).scaleb(-2)

    def test_rejects_unknown_currency(self):
        with pytest.raises(CurrencyError, match="Unsupported currency code"):
            from_minor_units(100, "ZZZ")

    def test_rejects_empty_currency(self):
        with pytest.raises(CurrencyError, match="must not be empty"):
            from_minor_units(100, "")

    def test_currency_code_is_case_insensitive(self):
        assert from_minor_units(250, "usd") == Decimal("2.50")
        assert from_minor_units(2500, "bhd") == Decimal("2.500")


# ---------------------------------------------------------------------------
# Round-trips
# ---------------------------------------------------------------------------


class TestRoundTrip:
    @pytest.mark.parametrize(
        "currency",
        ["USD", "EUR", "GBP", "JPY", "KRW", "BHD", "KWD", "INR", "VND", "OMR"],
    )
    def test_round_trip_is_lossless(self, currency):
        units = CURRENCY_MINOR_UNITS[currency]
        original = Decimal(123456).scaleb(-units)
        assert from_minor_units(to_minor_units(original, currency), currency) == original

    @pytest.mark.parametrize("minor", [0, 1, 7, 99, 100, 999, 1000, 123456])
    def test_round_trip_from_minor_units_is_lossless(self, minor):
        for currency in ("USD", "JPY", "BHD"):
            units = CURRENCY_MINOR_UNITS[currency]
            major = from_minor_units(minor, currency)
            assert to_minor_units(major, currency) == minor

    def test_round_trip_preserves_negative_sign(self):
        assert from_minor_units(to_minor_units(Decimal("-7.99"), "USD"), "USD") == Decimal("-7.99")
