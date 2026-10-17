"""Behavioural tests for ``app.core.secrets``.

Secret redaction is the last line of defence for admin API responses and
audit records, so each helper is exercised against realistic admin input:
config keys, free-text ``changes`` blobs, and hostile values.
"""

import pytest

from app.core.secrets import (
    REDACTED,
    SENSITIVE_KEY_PATTERNS,
    is_sensitive_config_key,
    mask_value,
    redact_secrets,
)


class TestIsSensitiveConfigKey:
    @pytest.mark.parametrize(
        "key",
        [
            "stripe_secret_key",
            "SMTP_PASSWORD",
            "api_key",
            "apikey",
            "service_token",
            "db_credential",
            "signing_secret",
            "webhook_url",
            "connection_string",
            "primary_dsn",
            "private_key",
        ],
    )
    def test_detects_secret_shaped_keys(self, key):
        assert is_sensitive_config_key(key) is True

    def test_detection_is_case_insensitive(self):
        assert is_sensitive_config_key("STRIPE_SECRET_KEY") is True
        assert is_sensitive_config_key("Api_Key") is True

    @pytest.mark.parametrize(
        "key",
        [
            "max_playback_quality",
            "feature_flags",
            "retention_days",
            "region",
            "display_name",
            "banner_url",
        ],
    )
    def test_plain_keys_are_not_sensitive(self, key):
        assert is_sensitive_config_key(key) is False

    def test_every_declared_pattern_matches_its_own_substring(self):
        # Guards against a pattern being added to SENSITIVE_KEY_PATTERNS that
        # the matcher cannot actually reach.
        for pattern in SENSITIVE_KEY_PATTERNS:
            assert is_sensitive_config_key(f"cfg_{pattern}") is True

    def test_empty_key_is_not_sensitive(self):
        assert is_sensitive_config_key("") is False


class TestMaskValue:
    def test_masks_any_value_to_fixed_marker(self):
        assert mask_value("sk_live_51H8xyz") == REDACTED
        assert mask_value("") == REDACTED

    def test_mask_is_stable_regardless_of_input_length(self):
        assert mask_value("a") == mask_value("a" * 500) == REDACTED

    def test_marker_does_not_leak_the_original(self):
        secret = "super-secret-token"
        assert secret not in mask_value(secret)


class TestRedactSecrets:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("token=abc123", f"token={REDACTED}"),
            ("token: abc123", f"token: {REDACTED}"),
            ("secret=abc123", f"secret={REDACTED}"),
            ("password=hunter2", f"password={REDACTED}"),
            ("passwd=hunter2", f"passwd={REDACTED}"),
            ("api_key=xyz", f"api_key={REDACTED}"),
            ("api-key=xyz", f"api-key={REDACTED}"),
            ("apikey=xyz", f"apikey={REDACTED}"),
            ("credential=xyz", f"credential={REDACTED}"),
            ("signing_secret=xyz", f"signing_secret={REDACTED}"),
            ("private_key=xyz", f"private_key={REDACTED}"),
            ("value=plainish", f"value={REDACTED}"),
        ],
    )
    def test_masks_each_supported_key_name(self, text, expected):
        assert redact_secrets(text) == expected

    def test_masks_multiple_fragments_in_one_blob(self):
        out = redact_secrets("value=alpha; token=beta, password=gamma")
        assert "alpha" not in out
        assert "beta" not in out
        assert "gamma" not in out
        assert out.count(REDACTED) == 3

    def test_masks_fragment_embedded_in_sentence(self):
        out = redact_secrets("rotated password=hunter2 during deploy window")
        assert "hunter2" not in out
        assert "rotated" in out and "deploy window" in out

    def test_stops_at_delimiters_so_neighbouring_fields_survive(self):
        out = redact_secrets("token=abc123&next=keepme")
        assert "abc123" not in out
        assert "next=keepme" in out

    def test_free_text_without_secrets_is_untouched(self):
        text = "severity=critical; removed 12 rows"
        assert redact_secrets(text) == text

    def test_none_and_empty_pass_through(self):
        assert redact_secrets(None) is None
        assert redact_secrets("") == ""

    def test_secret_value_with_no_key_is_not_guessed(self):
        # Redaction is key-driven: a bare token cannot be identified, so it is
        # left alone. Documents the boundary of the redaction guarantee.
        assert redact_secrets("the key is sk_live_51H8xyz") == "the key is sk_live_51H8xyz"

    def test_repeated_call_is_idempotent(self):
        once = redact_secrets("token=abc123")
        assert redact_secrets(once) == once
