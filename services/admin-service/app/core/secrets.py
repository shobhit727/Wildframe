"""Sensitive-value handling for admin configuration and audit records.

Secret-looking configuration keys (API keys, tokens, credentials, webhook
signing secrets, connection strings, ...) must never be returned through the
admin API or persisted into audit logs in plaintext.
"""

import re

REDACTED = "********"

# Substrings that mark a configuration key as holding a secret value.
SENSITIVE_KEY_PATTERNS = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "signing",
    "webhook",
    "connection_string",
    "dsn",
)

_KEY_VALUE_RE = re.compile(
    r"(?P<prefix>\b(?:value|token|secret|password|passwd|api[_-]?key|credential|"
    r"signing[_-]?secret|private[_-]?key)\s*[=:]\s*)(?P<value>[^\s,;&|]+)"
)


def is_sensitive_config_key(key: str) -> bool:
    """True when ``key`` looks like it holds a secret value."""
    lowered = key.lower()
    return any(pattern in lowered for pattern in SENSITIVE_KEY_PATTERNS)


def mask_value(_value: str) -> str:
    """Replace a secret value with a fixed redaction marker."""
    return REDACTED


def redact_secrets(text: str | None) -> str | None:
    """Mask secret-looking ``key=value`` / ``key: value`` fragments in text."""
    if not text:
        return text
    return _KEY_VALUE_RE.sub(lambda m: f"{m.group('prefix')}{REDACTED}", text)
