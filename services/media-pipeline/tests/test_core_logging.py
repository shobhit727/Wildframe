"""``app/core/logging.py`` — the correlation-id contextvar helpers.

This module is imported by nothing else in the service (the live wiring comes
from ``wildframe_observability``), so it is exercised here directly and by
observable behaviour: the returned ids, the contextvar they land in, and the
fact that ``set_request_id`` is isolated per asyncio task.
"""

import asyncio
import contextvars
import logging
import uuid

import pytest

from app.core.logging import request_id_var, set_correlation_id, set_request_id, setup_logging


def test_setup_logging_is_idempotent_and_does_not_clobber_a_configured_root():
    """basicConfig is a no-op once the root logger has handlers."""
    root = logging.getLogger()
    before = list(root.handlers)
    setup_logging()
    assert list(root.handlers) == before
    # A second call is still harmless.
    setup_logging()


def test_setup_logging_registers_a_handler_on_a_bare_root_logger():
    root = logging.getLogger()
    original_handlers, original_level = list(root.handlers), root.level
    root.handlers = []
    try:
        setup_logging()
        assert root.handlers, "setup_logging should install a handler"
        assert root.level == logging.INFO
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_set_request_id_returns_a_fresh_uuid4():
    first = set_request_id()
    second = set_request_id()
    assert first != second
    assert uuid.UUID(first).version == 4
    assert uuid.UUID(second).version == 4


def test_set_request_id_publishes_the_id_on_the_contextvar():
    rid = set_request_id()
    assert request_id_var.get() == rid


def test_the_request_id_contextvar_declares_an_empty_default():
    """A brand-new context (e.g. a worker thread) inherits no stale id."""
    assert contextvars.Context().run(request_id_var.get) == ""
    set_request_id()
    assert uuid.UUID(request_id_var.get())


def test_set_correlation_id_returns_the_id_it_is_given():
    assert set_correlation_id("corr-abc-123") == "corr-abc-123"
    assert set_correlation_id("") == ""


async def test_request_id_is_isolated_per_asyncio_task():
    """Two concurrent requests must not observe each other's id."""

    async def _worker(name: str) -> str:
        rid = set_request_id()
        await asyncio.sleep(0)
        assert request_id_var.get() == rid, name
        return rid

    a, b = await asyncio.gather(_worker("a"), _worker("b"))
    assert a != b
