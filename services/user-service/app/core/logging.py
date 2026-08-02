"""Logging configuration for User Service."""

import logging
import logging.config
from contextvars import ContextVar
from uuid import uuid4

from pythonjsonlogger import jsonlogger

# Context variables for correlation IDs
correlation_id: ContextVar[str] = ContextVar("correlation_id", default=str(uuid4()))
request_id: ContextVar[str] = ContextVar("request_id", default=str(uuid4()))


def set_correlation_id(cid: str | None = None) -> str:
    """Set or generate correlation ID."""
    if cid is None:
        cid = str(uuid4())
    correlation_id.set(cid)
    return cid


def set_request_id(rid: str | None = None) -> str:
    """Set or generate request ID."""
    if rid is None:
        rid = str(uuid4())
    request_id.set(rid)
    return rid


def get_correlation_id() -> str:
    """Get current correlation ID."""
    return correlation_id.get()


def get_request_id() -> str:
    """Get current request ID."""
    return request_id.get()


class ContextFilter(logging.Filter):
    """Add context variables to log records."""

    def filter(self, record):
        record.correlation_id = get_correlation_id()
        record.request_id = get_request_id()
        return True


def setup_logging(log_level: str = "INFO"):
    """Setup structured JSON logging."""
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": jsonlogger.JsonFormatter,
                "format": "%(timestamp)s %(level)s %(name)s %(message)s %(correlation_id)s %(request_id)s",
            },
            "standard": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s [correlation_id=%(correlation_id)s]",
            },
        },
        "filters": {
            "context": {
                "()": ContextFilter,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "json",
                "filters": ["context"],
            },
        },
        "root": {
            "level": log_level,
            "handlers": ["console"],
        },
        "loggers": {
            "app": {
                "level": log_level,
                "handlers": ["console"],
                "propagate": False,
            },
            "sqlalchemy": {
                "level": "WARNING",
                "handlers": ["console"],
                "propagate": False,
            },
            "aiokafka": {
                "level": "WARNING",
                "handlers": ["console"],
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(logging_config)
