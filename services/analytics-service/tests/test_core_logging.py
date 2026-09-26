"""Tests for ``analytics-service/app/core/logging.py``.

Like its ``streaming-service`` namesake this module is dead code: nothing in
``app/**`` imports it -- observability comes from the
``wildframe_observability`` package wired in ``app/main.py``. It is still part
of ``app/**`` and is a plausible future import target, so it is tested by
observable behaviour: the uuid that ``set_request_id`` returns, the ContextVar
value it stores, and the concrete logging configuration ``setup_logging``
produces. Nothing asserts on ``logging.basicConfig`` call internals.
"""

import contextlib
import logging
import uuid as uuid_module

import pytest

from app.core.logging import (
    request_id_var,
    set_correlation_id,
    set_request_id,
    setup_logging,
)


@contextlib.contextmanager
def bare_root():
    """Yield the root logger with no handlers, restoring it afterwards.

    ``logging.basicConfig`` is a no-op when the root logger already has
    handlers, and pytest's logging plugin installs several of its own, so the
    configuration assertions below have to run against a clean slate from
    *inside* the test body -- a fixture would be undone by the plugin between
    the setup and call phases.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    root.handlers = []
    try:
        yield root
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


# ================================= setup_logging ============================


@pytest.mark.unit
def test_setup_logging_installs_an_info_level_handler():
    """On a clean root logger the config is applied as documented."""
    with bare_root() as root:
        setup_logging()

        assert root.level == logging.INFO
        assert root.handlers, "setup_logging must attach at least one handler"
        handler = root.handlers[-1]
        assert isinstance(handler, logging.StreamHandler)
        # basicConfig filters on the *root* level, leaving the handler at NOTSET.
        assert handler.level == logging.NOTSET
        assert handler.formatter is not None
        assert handler.formatter._fmt == (
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )


@pytest.mark.unit
def test_setup_logging_emits_records_through_the_installed_handler():
    """A message logged after setup must reach a real stream handler."""
    with bare_root() as root:
        setup_logging()

        emitted: list[logging.LogRecord] = []
        handler = root.handlers[-1]
        original_emit = handler.emit
        handler.emit = emitted.append
        try:
            logging.getLogger("app.core.logging.probe").info("hello from probe")
        finally:
            handler.emit = original_emit

    matching = [r for r in emitted if r.getMessage() == "hello from probe"]
    assert matching, "the log record must reach the installed handler"
    assert matching[0].levelno == logging.INFO
    assert matching[0].name == "app.core.logging.probe"


@pytest.mark.unit
def test_setup_logging_filters_out_debug_records():
    """With the root logger at INFO, a DEBUG record is dropped."""
    with bare_root() as root:
        setup_logging()

        emitted: list[logging.LogRecord] = []
        handler = root.handlers[-1]
        original_emit = handler.emit
        handler.emit = emitted.append
        try:
            logger = logging.getLogger("app.core.logging.probe")
            logger.debug("should be filtered")
            logger.warning("should survive")
        finally:
            handler.emit = original_emit

    messages = [r.getMessage() for r in emitted]
    assert "should survive" in messages
    assert "should be filtered" not in messages


@pytest.mark.unit
def test_setup_logging_is_a_noop_when_the_root_already_has_handlers():
    """KNOWN LIMITATION -- app/core/logging.py:12-14.

    ``logging.basicConfig`` does nothing when the root logger already has
    handlers. In the running service that is always the case: importing
    ``app.main`` pulls in ``wildframe_observability``, whose
    ``wire_observability`` calls its own ``dictConfig`` and replaces the root
    handlers. So this module's ``setup_logging`` can never take effect in
    production -- one of the reasons it is unused.

    Pinned so the limitation is explicit; if the module is ever wired in
    behind ``force=True`` this test fails.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        existing = logging.StreamHandler()
        root.handlers = [existing]
        root.setLevel(logging.WARNING)

        setup_logging()

        assert root.level == logging.WARNING, "basicConfig did not override"
        assert root.handlers == [existing]
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


# ============================== set_request_id ==============================


@pytest.mark.unit
def test_set_request_id_returns_a_uuid4_string():
    """The returned value is the generated id, not None."""
    rid = set_request_id()

    assert isinstance(rid, str)
    # Round-trips through uuid.UUID, so it really is a UUID.
    parsed = uuid_module.UUID(rid)
    assert str(parsed) == rid
    assert parsed.version == 4


@pytest.mark.unit
def test_set_request_id_stores_the_value_in_the_context_var():
    """The ContextVar must hold exactly the id that was returned."""
    rid = set_request_id()
    assert request_id_var.get() == rid


@pytest.mark.unit
def test_set_request_id_generates_a_fresh_id_each_call():
    """Two calls must not collide -- a reused id would break log tracing."""
    first = set_request_id()
    second = set_request_id()

    assert first != second
    assert request_id_var.get() == second


@pytest.mark.unit
def test_request_id_var_is_named_request_id():
    """The ContextVar carries the documented name."""
    assert request_id_var.name == "request_id"


# ============================ set_correlation_id ============================


@pytest.mark.unit
def test_set_correlation_id_returns_the_value_unchanged():
    """The helper is a pass-through: it echoes the caller's id."""
    assert set_correlation_id("corr-abc") == "corr-abc"
    assert set_correlation_id("") == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "cid", ["corr-1", "0123456789abcdef", "corr with spaces", "corr/slash"]
)
def test_set_correlation_id_accepts_any_string(cid):
    """No validation, no normalisation -- the caller's value is returned."""
    assert set_correlation_id(cid) == cid


@pytest.mark.unit
def test_set_correlation_id_does_not_touch_the_request_id_var():
    """The two identifiers are independent, and this module has only one var.

    Unlike the streaming service -- which carries both a correlation and a
    request ContextVar plus a ``ContextFilter`` that stamps both -- this module
    declares only ``request_id_var`` and its ``set_correlation_id`` stores
    nothing. Pinned so the asymmetry is explicit.
    """
    before = request_id_var.get()
    set_correlation_id("corr-xyz")

    assert request_id_var.get() == before
    assert not hasattr(type(request_id_var), "correlation_id")
