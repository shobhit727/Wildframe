"""
Structured logging configuration for Auth Service.
Implements JSON logging with correlation IDs for distributed tracing.
"""

import logging
import logging.config
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.settings import settings
from pythonjsonlogger import jsonlogger

# Context variables for distributed tracing
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Headers whose values MUST never appear in logs. Header names are matched
# case-insensitively against the JSON top-level fields produced by
# python-json-logger (callers log headers via extra={...}).
_REDACTED_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})
_REDACTED_VALUE = "[REDACTED]"
_CONTROL_CHARS = "".join(
    chr(c) for c in range(32) if c not in (9,)  # keep tab, drop the rest
) + "\x7f"
_CONTROL_TRANSLATION = str.maketrans({c: "?" for c in _CONTROL_CHARS})

def _redact_headers(log_record: dict[str, Any]) -> None:
    """Mask Authorization/Cookie/Set-Cookie values in-place on the log record."""
    for key in list(log_record):
        if key.lower() in _REDACTED_HEADERS:
            log_record[key] = _REDACTED_VALUE


class HeaderRedactionFilter(logging.Filter):
    """Logging filter that masks Authorization/Cookie/Set-Cookie values.

    Inspects both the rendered log message string and any ``extra`` fields
    passed via the LogRecord. Header names are matched case-insensitively.
    Safe to attach to any handler or logger — it never mutates the message
    to the point of breaking downstream formatters.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # 1. Sanitize the rendered message for log-injection vectors.
        msg = record.getMessage()
        if msg:
            record.msg = _sanitize_message(msg)
            record.args = ()

        # 2. Walk the record's own attributes (covers extra= fields too,
        # since stdlib logging stores them on the record via setattr).
        for attr in list(vars(record)):
            if attr.startswith("_"):
                continue
            value = getattr(record, attr, None)
            if isinstance(value, str) and attr.lower() in _REDACTED_HEADERS:
                setattr(record, attr, _REDACTED_VALUE)

        return True


def _sanitize_message(message: str) -> str:
    """Escape log-injection vectors (newlines, CR, NUL, control bytes)."""
    if not message:
        return message
    # Drop carriage returns and newlines entirely so an attacker cannot forge
    # extra log lines; map other control bytes to '?'.
    message = message.replace("\r", "?").replace("\n", "?")
    return message.translate(_CONTROL_TRANSLATION)


class CorrelationIdJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter that adds correlation ID and request context."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Add custom fields to log record.

        Args:
            log_record: The log record dictionary
            record: The logging record
            message_dict: The message dictionary
        """
        super().add_fields(log_record, record, message_dict)

        # Add timestamp in ISO format
        log_record["timestamp"] = datetime.now(UTC).isoformat()

        # Add correlation information
        log_record["correlation_id"] = correlation_id_var.get()
        log_record["request_id"] = request_id_var.get()
        log_record["user_id"] = user_id_var.get()

        # Add service information
        log_record["service"] = settings.SERVICE_NAME
        log_record["version"] = settings.SERVICE_VERSION
        log_record["environment"] = settings.ENVIRONMENT

        # Add logging level name
        log_record["level"] = record.levelname

        # Defuse log injection and redact sensitive headers before serialization.
        if isinstance(log_record.get("message"), str):
            log_record["message"] = _sanitize_message(log_record["message"])
        _redact_headers(log_record)



def setup_logging() -> None:
    """Configure structured logging with JSON output."""

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            },
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
            },
            "json": {
                "()": "app.core.logging.CorrelationIdJsonFormatter",
                "format": "%(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": settings.LOG_LEVEL,
                "formatter": "json" if settings.ENVIRONMENT != "development" else "detailed",
                "stream": "ext://sys.stdout",
                "filters": ["redact_headers"],
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "INFO",
                "formatter": "json",
                "filename": "logs/auth-service.log",
                "maxBytes": 10485760,  # 10MB
                "backupCount": 10,
                "filters": ["redact_headers"],
            },
        },
        "filters": {
            "redact_headers": {
                "()": "app.core.logging.HeaderRedactionFilter",
            },
        },
        "loggers": {
            "": {
                "level": settings.LOG_LEVEL,
                "handlers": ["console", "file"],
                "propagate": False,
            },
            "sqlalchemy": {
                "level": "WARNING",
                "handlers": ["console"],
                "propagate": False,
            },
            "asyncio": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(logging_config)
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging initialized for {settings.SERVICE_NAME} " f"(environment: {settings.ENVIRONMENT})"
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        logging.Logger: Configured logger
    """
    return logging.getLogger(name)


def set_correlation_id(correlation_id: str | None = None) -> str:
    """Set correlation ID for request tracking.

    Args:
        correlation_id: Optional correlation ID. If None, generates a new one.

    Returns:
        str: The correlation ID
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    correlation_id_var.set(correlation_id)
    return correlation_id


def set_request_id(request_id: str | None = None) -> str:
    """Set request ID for current request.

    Args:
        request_id: Optional request ID. If None, generates a new one.

    Returns:
        str: The request ID
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    request_id_var.set(request_id)
    return request_id


def set_user_id(user_id: str) -> None:
    """Set user ID for current request context.

    Args:
        user_id: The user ID
    """
    user_id_var.set(user_id)
