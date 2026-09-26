"""Behavioural tests for app/core/logging.py.

The module configures JSON logging via ``logging.config.dictConfig`` and keeps
the correlation/request IDs in :class:`contextvars.ContextVar` so every log
record emitted inside a request carries them. These tests assert observable
behaviour (handler wiring, JSON payload contents, context-var propagation)
rather than private internals.
"""

import contextvars
import io
import json
import logging
import os

import pytest

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

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """setup_logging() rewrites the root logger via dictConfig — undo that.

    Without this the JSON file/console handlers installed by one test would
    leak into every later test in the session.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_disabled = logging.root.manager.disable
    saved_ids = (correlation_id.get(), request_id.get())
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        logging.disable(saved_disabled)
        correlation_id.set(saved_ids[0])
        request_id.set(saved_ids[1])


def _record(name: str = "app.test") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


class TestCorrelationContextVars:
    def test_ids_start_empty(self):
        assert get_correlation_id() is None
        assert get_request_id() is None

    def test_setters_are_visible_to_getters(self):
        set_correlation_id("cid-123")
        set_request_id("rid-456")

        assert get_correlation_id() == "cid-123"
        assert get_request_id() == "rid-456"

    def test_setters_are_visible_to_the_raw_contextvars(self):
        set_correlation_id("cid-raw")
        set_request_id("rid-raw")

        assert correlation_id.get() == "cid-raw"
        assert request_id.get() == "rid-raw"

    def test_a_fresh_context_does_not_inherit_request_ids(self):
        set_correlation_id("cid-root")
        set_request_id("rid-root")

        # A brand new context (e.g. a worker thread, or a fresh request scope)
        # starts from the ContextVar defaults rather than the caller's values.
        fresh = contextvars.Context()

        assert fresh.run(get_correlation_id) is None
        assert fresh.run(get_request_id) is None
        assert get_correlation_id() == "cid-root"


class TestContextFilter:
    def test_filter_injects_ids_and_always_keeps_record(self):
        set_correlation_id("cid-filter")
        set_request_id("rid-filter")
        record = _record()

        assert ContextFilter().filter(record) is True
        assert record.correlation_id == "cid-filter"
        assert record.request_id == "rid-filter"

    def test_filter_sets_none_when_no_request_context(self):
        correlation_id.set(None)
        request_id.set(None)
        record = _record()

        ContextFilter().filter(record)

        assert record.correlation_id is None
        assert record.request_id is None


class TestSetupLogging:
    def test_under_pytest_no_file_handler_is_installed(self, monkeypatch):
        """PYTEST_CURRENT_TEST is set while tests run, so no log file is opened."""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test::case (call)")

        setup_logging()

        root = logging.getLogger()
        classes = {type(h).__name__ for h in root.handlers}
        assert "StreamHandler" in classes
        assert "FileHandler" not in classes

    def test_outside_pytest_a_file_handler_is_written(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.chdir(tmp_path)

        setup_logging()

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers, "expected a file handler outside the test environment"
        assert file_handlers[0].level == logging.INFO
        assert os.path.basename(file_handlers[0].baseFilename) == "content_service.log"
        assert os.path.exists(os.path.join(tmp_path, "content_service.log"))

    def test_console_handler_writes_json_with_correlation_ids(self, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test::case (call)")
        setup_logging()
        set_correlation_id("cid-json")
        set_request_id("rid-json")

        # Attach our own stream to the console handler dictConfig installed so
        # the assertion reads the record the *configured* pipeline produced.
        console = next(
            h for h in logging.getLogger().handlers if type(h).__name__ == "StreamHandler"
        )
        stream = io.StringIO()
        console.setStream(stream)
        logging.getLogger("app.probe").info("probe-message")

        payload = json.loads(stream.getvalue().strip())
        assert payload["message"] == "probe-message"
        assert payload["levelname"] == "INFO"
        assert payload["name"] == "app.probe"
        assert payload["correlation_id"] == "cid-json"
        assert payload["request_id"] == "rid-json"
        assert "asctime" in payload

    def test_root_logger_and_noisy_loggers_are_tuned(self, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test::case (call)")

        setup_logging()

        assert logging.getLogger().level == logging.DEBUG
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
        assert logging.getLogger("sqlalchemy.pool").level == logging.WARNING

    def test_setup_logging_is_idempotent(self, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test::case (call)")

        setup_logging()
        first = logging.getLogger().handlers[:]
        setup_logging()
        second = logging.getLogger().handlers[:]

        assert [type(h) for h in first] == [type(h) for h in second]
