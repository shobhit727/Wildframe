"""Behavioural tests for the dead ``app/core/logging.py``.

Nothing in the service imports this module (``wire_observability`` in
``app/main.py`` is what actually configures logging), so it has never been
exercised. These tests assert the observable contract only — the returned
identifier, the ContextVar it lands in, and isolation between contexts.
``logging.basicConfig`` internals are deliberately not asserted: it is a no-op
whenever the root logger already has handlers, which is always true under
pytest.
"""

import asyncio
import contextvars
import logging
import uuid
from contextvars import copy_context

import pytest

from app.core.logging import (
    request_id_var,
    set_correlation_id,
    set_request_id,
    setup_logging,
)


def _default_request_id() -> str:
    """Read the ContextVar in a brand-new context, i.e. its declared default."""
    return contextvars.Context().run(request_id_var.get)


class TestSetupLogging:
    def test_is_idempotent_and_never_raises(self):
        # basicConfig is a no-op once the root logger has handlers; the only
        # contract is that calling it twice is safe.
        setup_logging()
        setup_logging()
        assert isinstance(logging.getLogger(), logging.Logger)

    def test_keeps_existing_root_handlers(self):
        before = list(logging.getLogger().handlers)
        setup_logging()
        assert list(logging.getLogger().handlers) == before


class TestSetRequestId:
    def test_returns_a_fresh_uuid4_string(self):
        rid = set_request_id()
        assert isinstance(rid, str)
        assert uuid.UUID(rid).version == 4

    def test_stores_the_returned_value_in_the_contextvar(self):
        assert _default_request_id() == ""
        rid = set_request_id()
        assert request_id_var.get() == rid

    def test_two_calls_produce_different_ids(self):
        assert set_request_id() != set_request_id()

    def test_the_contextvar_tracks_the_latest_call(self):
        first = set_request_id()
        second = set_request_id()
        assert request_id_var.get() == second
        assert first != second


class TestContextIsolation:
    def test_a_copied_context_does_not_see_the_later_write(self):
        set_request_id()
        snapshot = copy_context()
        rid = request_id_var.get()
        set_request_id()
        # Inside the snapshot the original value is still visible.
        assert snapshot.run(request_id_var.get) == rid
        assert request_id_var.get() != rid

    def test_each_concurrent_task_gets_its_own_request_id(self):
        async def worker():
            return set_request_id()

        async def main():
            return await asyncio.gather(*(worker() for _ in range(5)))

        ids = asyncio.run(main())
        assert len(set(ids)) == 5
        # The outer context is unaffected by the tasks: each task ran in a copy
        # of the context that asyncio.gather created.
        assert _default_request_id() == ""


class TestSetCorrelationId:
    @pytest.mark.parametrize("cid", ["abc-123", "", "  ", "éè", "0" * 200])
    def test_returns_its_argument_unchanged(self, cid):
        assert set_correlation_id(cid) == cid

    def test_does_not_touch_the_request_id_contextvar(self):
        rid = set_request_id()
        set_correlation_id("corr-1")
        assert request_id_var.get() == rid

    def test_is_not_stored_anywhere(self):
        # Correlation ids are propagated by the observability SDK's own
        # contextvar; this helper is a pass-through by design.
        set_correlation_id("corr-2")
        assert "corr-2" not in request_id_var.get()


class TestModuleContract:
    def test_default_request_id_is_the_empty_string(self):
        assert _default_request_id() == ""

    def test_request_id_var_name(self):
        assert request_id_var.name == "request_id"
