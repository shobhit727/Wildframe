"""Tests for ``streaming-service/app/core/logging.py``.

This module is dead code in production: nothing imports it -- observability
comes from the ``wildframe_observability`` package wired in
``app/main.py:108``. It is nevertheless part of ``app/**`` and is a plausible
future import target, so it is tested by behaviour rather than skipped.

The module owns two ``ContextVar``s plus a ``ContextFilter`` that stamps them
onto every ``LogRecord``. Because ``ContextVar.set`` has no return value, the
getters are the only way to read the value back, which is what these tests do.
"""

import json
import logging

import pytest
from pythonjsonlogger import jsonlogger

from app.core import logging as streaming_logging
from app.core.logging import (
    ContextFilter,
    correlation_id,
    get_correlation_id,
    get_request_id,
    request_id,
    set_correlation_id,
    set_request_id,
    setup_logging,
)


@pytest.fixture(autouse=True)
def clean_context():
    """ContextVar.set is irreversible; reset the module vars around each test."""
    yield
    correlation_id.set(None)
    request_id.set(None)


# ------------------------------------------------------------- getters ------


@pytest.mark.unit
def test_getters_default_to_none():
    """Both context vars are declared with a ``None`` default."""
    assert get_correlation_id() is None
    assert get_request_id() is None


@pytest.mark.unit
def test_setters_are_observable_through_the_getters():
    """set_correlation_id / set_request_id must round-trip to their getters."""
    set_correlation_id("corr-123")
    set_request_id("req-456")

    assert get_correlation_id() == "corr-123"
    assert get_request_id() == "req-456"
    # The ContextVar objects themselves hold the same values.
    assert correlation_id.get() == "corr-123"
    assert request_id.get() == "req-456"


@pytest.mark.unit
def test_setters_overwrite_previous_values():
    """A later set wins -- a second request reuses the process context."""
    set_correlation_id("first")
    set_request_id("req-1")
    set_correlation_id("second")
    set_request_id("req-2")

    assert get_correlation_id() == "second"
    assert get_request_id() == "req-2"


# ---------------------------------------------------------- ContextFilter ---


@pytest.mark.unit
def test_context_filter_stamps_both_ids_and_accepts_the_record():
    """``filter`` must attach both ids and return True (never drop records)."""
    set_correlation_id("cid-filter")
    set_request_id("rid-filter")

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    assert not hasattr(record, "correlation_id")

    assert ContextFilter().filter(record) is True

    assert record.correlation_id == "cid-filter"
    assert record.request_id == "rid-filter"
    # The original record is otherwise untouched.
    assert record.getMessage() == "hello world"


@pytest.mark.unit
def test_context_filter_stamps_none_when_context_is_unset():
    """An unstamped record gets None rather than raising."""
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="no context",
        args=None,
        exc_info=None,
    )
    assert ContextFilter().filter(record) is True
    assert record.correlation_id is None
    assert record.request_id is None


@pytest.mark.unit
def test_context_filter_reads_context_at_filter_time():
    """Values are pulled from the ContextVars per record, not captured once."""
    context_filter = ContextFilter()

    set_correlation_id("cid-a")
    set_request_id("rid-a")
    first = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
    context_filter.filter(first)

    set_correlation_id("cid-b")
    set_request_id("rid-b")
    second = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
    context_filter.filter(second)

    assert (first.correlation_id, first.request_id) == ("cid-a", "rid-a")
    assert (second.correlation_id, second.request_id) == ("cid-b", "rid-b")


# --------------------------------------------------------- setup_logging ----


@pytest.mark.unit
def test_setup_logging_installs_a_json_formatter_with_the_context_filter():
    """dictConfig must wire the JSON formatter onto the console handler.

    Restores the root logger afterwards so the rest of the suite is unaffected.
    """
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        setup_logging()

        console = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert console, "expected the root logger to have a console handler"
        handler = console[0]
        assert handler.level == logging.DEBUG
        assert isinstance(handler.formatter, streaming_logging.jsonlogger.JsonFormatter)
        assert any(isinstance(f, ContextFilter) for f in handler.filters)
        assert root.level == logging.DEBUG
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        root.setLevel(logging.WARNING)


@pytest.mark.unit
def test_setup_logging_emits_json_containing_the_correlation_ids(caplog):
    """End-to-end: a logged message is serialised with both ids embedded.

    This is the behaviour the module exists for -- a JSON line that can be
    correlated across services.
    """
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        setup_logging()
        # Reuse the exact formatter dictConfig installed, so this asserts the
        # configured format string rather than a locally rebuilt one.
        installed = next(
            h for h in root.handlers if isinstance(h.formatter, jsonlogger.JsonFormatter)
        )
        stream = _StringHandler(installed.formatter)
        root.addHandler(stream)
        root.setLevel(logging.DEBUG)

        set_correlation_id("cid-json")
        set_request_id("rid-json")
        logging.getLogger("app.core.logging.probe").info("structured hello")

        payload = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert payload["message"] == "structured hello"
        assert payload["correlation_id"] == "cid-json"
        assert payload["request_id"] == "rid-json"
        assert payload["levelname"] == "INFO"
        assert payload["name"] == "app.core.logging.probe"
        assert "asctime" in payload
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        root.setLevel(logging.WARNING)


class _StringHandler(logging.Handler):
    """Minimal in-memory stream handler so the JSON line can be inspected."""

    def __init__(self, formatter: logging.Formatter):
        super().__init__()
        self.setFormatter(formatter)
        self._buf = ""

    def emit(self, record):
        self._buf += self.format(record) + "\n"

    def getvalue(self):
        return self._buf


@pytest.mark.unit
def test_module_uses_a_contextvar_per_identifier():
    """The two identifiers must be independent ContextVars, not one shared."""
    assert correlation_id is not request_id
    assert correlation_id.name == "correlation_id"
    assert request_id.name == "request_id"
