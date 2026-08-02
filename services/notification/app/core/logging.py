"""Logging configuration."""

import logging
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def setup_logging() -> None:
    """Setup application logging."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def set_request_id() -> str:
    """Set request ID."""
    rid = str(uuid.uuid4())
    request_id_var.set(rid)
    return rid


def set_correlation_id(cid: str) -> str:
    """Set correlation ID."""
    return cid
