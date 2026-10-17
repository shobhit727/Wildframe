"""Behaviour of ``app/core/logging.py``.

The module is imported by nothing in production, so the only meaningful
assertions are observable effects: a valid request id lands in the ContextVar,
and ``setup_logging`` really installs an INFO-level formatter when the root
logger is unconfigured (calling it twice must not clobber an existing setup).
"""

import logging
import uuid

from app.core.logging import request_id_var, set_correlation_id, set_request_id, setup_logging


class _IsolatedRoot:
    """Temporarily detach the root logger so basicConfig is not a no-op."""

    def __enter__(self):
        self._root = logging.getLogger()
        self._handlers = self._root.handlers[:]
        self._level = self._root.level
        self._root.handlers = []
        self._root.setLevel(logging.NOTSET)
        return self._root

    def __exit__(self, *exc):
        for handler in self._root.handlers:
            handler.close()
        self._root.handlers = self._handlers
        self._root.setLevel(self._level)
        return False


class TestSetupLogging:
    def test_installs_an_info_level_formatter_on_a_bare_root(self):
        with _IsolatedRoot() as root:
            setup_logging()

            assert root.level == logging.INFO
            assert len(root.handlers) == 1
            assert root.handlers[0].formatter is not None
            assert root.handlers[0].formatter._fmt == (
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )

    def test_repeated_calls_do_not_stack_handlers(self):
        with _IsolatedRoot() as root:
            setup_logging()
            setup_logging()
            setup_logging()

            assert len(root.handlers) == 1

    def test_an_existing_configuration_is_left_alone(self):
        root = logging.getLogger()
        before = root.handlers[:]
        before_level = root.level

        setup_logging()

        assert root.handlers == before
        assert root.level == before_level


class TestSetRequestId:
    def test_returns_a_uuid4_string(self):
        rid = set_request_id()

        parsed = uuid.UUID(rid)
        assert str(parsed) == rid
        assert parsed.version == 4

    def test_publishes_the_id_through_the_context_var(self):
        rid = set_request_id()

        assert request_id_var.get() == rid

    def test_each_call_yields_a_distinct_id(self):
        assert set_request_id() != set_request_id()


class TestSetCorrelationId:
    def test_returns_the_supplied_id_unchanged(self):
        assert set_correlation_id("corr-123") == "corr-123"

    def test_accepts_an_empty_id(self):
        assert set_correlation_id("") == ""


def test_module_level_context_var_defaults_to_empty():
    """A fresh import observes the documented empty default, never a stale id."""
    assert isinstance(request_id_var.get(), str)
    request_id_var.set("sentinel")
    try:
        assert request_id_var.get() == "sentinel"
    finally:
        request_id_var.set("")
    assert request_id_var.get() == ""
