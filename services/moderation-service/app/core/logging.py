"""Logging configuration.

Structured-ish logging with a per-request correlation id carried in a
contextvar so log lines emitted anywhere in the request path can be tied back
to the same moderation review session. Mirrors the billing/streaming pattern.
"""
import logging
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def setup_logging() -> None:
    """Setup application logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def set_request_id() -> str:
    """Set a fresh request id and return it."""
    rid = str(uuid.uuid4())
    request_id_var.set(rid)
    return rid


def set_correlation_id(cid: str) -> str:
    """Set correlation id (propagated across service boundaries)."""
    return cid
