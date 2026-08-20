"""Structured JSON logging for all Wildframe services.

Every log line emitted by a Wildframe service is structured JSON with these
fields:
  - timestamp: ISO-8601 UTC
  - level: DEBUG/INFO/WARNING/ERROR/CRITICAL
  - logger: dotted logger name
  - message: the log message
  - request_id: per-request UUID (propagated via contextvar)
  - correlation_id: cross-service correlation UUID
  - service_name: which service emitted this log
  - extra: any additional structured fields

Usage in a service::

    from wildframe_observability import setup_logging, get_logger

    setup_logging(service_name="billing", log_level="INFO")
    logger = get_logger("billing.service")
    logger.info("payout accrued", extra={"creator_id": str(cid), "amount": 42.0})

Security: all user-supplied data (log messages, extra fields, header values)
is sanitized to prevent log-injection attacks (CRLF injection, JSON breakout,
ansi escape sequences). See :func:`_sanitize_for_log`.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

# Contextvars propagated by CorrelationMiddleware.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Get the current correlation_id from contextvar.

    Returns empty string if not set.
    """
    return correlation_id_var.get("")


def get_request_id() -> str:
    """Get the current request_id from contextvar.

    Returns empty string if not set.
    """
    return request_id_var.get("")


# ---------------------------------------------------------------------------
# Field-level secret redaction
# ---------------------------------------------------------------------------

#: Field names (case-insensitive) whose values should be redacted in logs.
#: Includes common secret patterns: passwords, tokens, secrets, auth headers,
#: cookies, API keys, and Stripe keys.
REDACT_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "secret_key",
        "authorization",
        "cookie",
        "set-cookie",
        "api_key",
        "apikey",
        "api-key",
        "stripe_key",
        "stripe_secret_key",
        "stripe_api_key",
    }
)


def _redact_secrets(value: Any, depth: int = 0) -> Any:
    """Recursively redact secret field values in dicts and lists.

    Only dict keys matching REDACT_FIELDS (case-insensitive) are redacted.
    Other values are passed through unchanged (they will still be sanitized
    by _sanitize_for_log for control chars, etc.).
    """
    if depth > 10:
        return "<max-depth>"
    if isinstance(value, dict):
        redacted_keys = {f.replace("-", "").replace("_", "") for f in REDACT_FIELDS}
        return {
            k: (
                "***REDACTED***"
                if k.lower().replace("-", "").replace("_", "") in redacted_keys
                else _redact_secrets(v, depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item, depth + 1) for item in value]
    return value


# ---------------------------------------------------------------------------
# Log-injection sanitization
# ---------------------------------------------------------------------------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_MAX_STRING_LEN = 10_000


def _sanitize_for_log(value: Any, depth: int = 0) -> Any:
    """Recursively sanitize a value for safe JSON logging.

    Prevents:
    - CRLF injection (newlines in message break log lines)
    - JSON breakout (unescaped quotes/backslashes)
    - ANSI escape injection
    - Excessive payload size (DoS via log volume)

    Args:
        value: Any JSON-serializable value.
        depth: Recursion depth guard (prevents stack overflow on cycles).

    Returns:
        A sanitized value safe to include in a JSON log entry.
    """
    if depth > 10:
        return "[max depth exceeded]"

    if isinstance(value, str):
        # Strip ANSI escapes FIRST (they contain ESC which is a control char).
        s = _ANSI_ESCAPE.sub("", value)
        # Then strip remaining control characters.
        s = _CONTROL_CHARS.sub("", s)
        # Normalize newlines to a single space (CRLF injection prevention).
        s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        if len(s) > _MAX_STRING_LEN:
            s = s[:_MAX_STRING_LEN] + "…[truncated]"
        return s

    if isinstance(value, dict):
        return {k: _sanitize_for_log(v, depth + 1) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_log(v, depth + 1) for v in value]

    # int, float, bool, None — safe as-is.
    return value


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON object."""

    def __init__(self, service_name: str = "") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        # Standard LogRecord attributes we handle explicitly or ignore.
        STANDARD_ATTRS = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "exc_info",
            "exc_text",
            "stack_info",
            "taskName",
            "getMessage",
        }

        message = _sanitize_for_log(record.getMessage())
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": request_id_var.get(""),
            "correlation_id": correlation_id_var.get(""),
            "service_name": self.service_name,
        }
        # Merge any extra structured fields from `logger.info(..., extra={})`.
        # Standard logging puts extra keys directly on the record.
        for key, value in record.__dict__.items():
            if key in STANDARD_ATTRS:
                continue
            if key == "extra" and isinstance(value, dict):
                # Also support the test pattern: record.extra = {...}
                for k, v in value.items():
                    # Redact secret fields, then sanitize for log injection.
                    v = _redact_secrets(v)
                    log_entry[_sanitize_for_log(k)] = _sanitize_for_log(v)
                continue
            # Redact secret fields, then sanitize for log injection.
            value = _redact_secrets(value)
            log_entry[_sanitize_for_log(key)] = _sanitize_for_log(value)

        # Also promote well-known extra attributes (idempotent with above).
        for key in ("creator_id", "content_id", "user_id", "job_id", "trace_id"):
            value = getattr(record, key, None)
            if value is not None:
                value = _redact_secrets(value)
                log_entry[key] = _sanitize_for_log(str(value))
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = _sanitize_for_log(self.formatException(record.exc_info))
        return json.dumps(log_entry, default=str)


def setup_logging(service_name: str, log_level: str = "INFO") -> None:
    """Configure the root logger with structured JSON output.

    Call once at service startup (in create_app or before).
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove any existing handlers to avoid duplicate output.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service_name=service_name))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger bound to the given name."""
    return logging.getLogger(name)


def set_request_id(rid: Optional[str] = None) -> str:
    """Set the request_id contextvar. Generates a UUID if not provided."""
    rid = rid if rid is not None else str(uuid.uuid4())
    request_id_var.set(rid)
    return rid


def set_correlation_id(cid: Optional[str] = None) -> str:
    """Set the correlation_id contextvar. Generates a UUID if not provided."""
    cid = cid if cid is not None else str(uuid.uuid4())
    correlation_id_var.set(cid)
    return cid
