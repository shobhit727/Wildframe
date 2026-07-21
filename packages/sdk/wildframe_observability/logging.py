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
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

# Contextvars propagated by CorrelationMiddleware.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON object."""

    def __init__(self, service_name: str = "") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(""),
            "correlation_id": correlation_id_var.get(""),
            "service_name": self.service_name,
        }
        # Merge any extra structured fields from `logger.info(..., extra={})`.
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            log_entry.update(record.extra)
        # Also promote well-known extra attributes.
        for key in ("creator_id", "content_id", "user_id", "job_id", "trace_id"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = str(value)
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
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
    rid = rid or str(uuid.uuid4())
    request_id_var.set(rid)
    return rid


def set_correlation_id(cid: Optional[str] = None) -> str:
    """Set the correlation_id contextvar. Generates a UUID if not provided."""
    cid = cid or str(uuid.uuid4())
    correlation_id_var.set(cid)
    return cid
