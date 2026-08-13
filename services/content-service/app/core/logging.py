"""
Structured JSON logging setup with correlation IDs.
Enables distributed tracing and debugging across services.
"""

import logging
import logging.config
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

# Context variables for tracking request flow
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class ContextFilter(logging.Filter):
    """Add correlation and request IDs to log records."""

    def filter(self, record):
        record.correlation_id = get_correlation_id()
        record.request_id = get_request_id()
        return True


def setup_logging():
    """Configure JSON logging with correlation IDs."""
    import os

    # Skip file handler in test environments to avoid permission issues
    in_test = os.getenv("PYTEST_CURRENT_TEST") is not None

    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "json",
            "filters": ["context_filter"],
        },
    }
    if not in_test:
        handlers["file"] = {
            "class": "logging.FileHandler",
            "level": "INFO",
            "formatter": "json",
            "filename": "content_service.log",
            "filters": ["context_filter"],
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": jsonlogger.JsonFormatter,
                    "format": "%(asctime)s %(name)s %(levelname)s %(message)s %(correlation_id)s %(request_id)s",
                },
                "standard": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
            },
            "filters": {"context_filter": {"()": ContextFilter}},
            "handlers": handlers,
            "loggers": {
                "": {"handlers": list(handlers.keys()), "level": "DEBUG", "propagate": True},
                "sqlalchemy.engine": {"level": "WARNING"},
                "sqlalchemy.pool": {"level": "WARNING"},
            },
        }
    )


def set_correlation_id(cid: str):
    """Set correlation ID for request tracing."""
    correlation_id.set(cid)


def set_request_id(rid: str):
    """Set request ID for request tracing."""
    request_id.set(rid)


def get_correlation_id() -> str | None:
    """Get current correlation ID."""
    return correlation_id.get()


def get_request_id() -> str | None:
    """Get current request ID."""
    return request_id.get()
