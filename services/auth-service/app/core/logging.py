"""
Structured logging configuration for Auth Service.
Implements JSON logging with correlation IDs for distributed tracing.
"""

import logging
import logging.config
import json
from datetime import datetime
from typing import Any
from pythonjsonlogger import jsonlogger
from contextvars import ContextVar
import uuid

from app.core.settings import settings

# Context variables for distributed tracing
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


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
        log_record["timestamp"] = datetime.utcnow().isoformat()

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
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "INFO",
                "formatter": "json",
                "filename": "logs/auth-service.log",
                "maxBytes": 10485760,  # 10MB
                "backupCount": 10,
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
        f"Logging initialized for {settings.SERVICE_NAME} "
        f"(environment: {settings.ENVIRONMENT})"
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
