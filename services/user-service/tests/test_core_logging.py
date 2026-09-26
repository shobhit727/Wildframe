"""Behavioural tests for `app.core.logging` (user-service).

Covers the four ContextVar-backed id helpers and the real effect of
`setup_logging()`: it installs a dictConfig whose console handler carries the
`ContextFilter`, the JSON formatter and the requested level. Assertions are made
against the *installed logging configuration* (logger levels, handler classes,
formatters, filters) rather than against `logging.basicConfig` internals, because
this service uses `dictConfig`.
"""

import json
import logging
import logging.config
import uuid

import pytest
from pythonjsonlogger import jsonlogger

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
def _restore_logging_config():
    """dictConfig mutates global logging state - snapshot and restore it."""
    root = logging.getLogger()
    before = {
        "root_handlers": list(root.handlers),
        "root_level": root.level,
        "app": logging.getLogger("app").level,
        "sqlalchemy": logging.getLogger("sqlalchemy").level,
        "aiokafka": logging.getLogger("aiokafka").level,
    }
    yield
    root.handlers = before["root_handlers"]
    root.setLevel(before["root_level"])
    for name in ("app", "sqlalchemy", "aiokafka"):
        logger = logging.getLogger(name)
        logger.setLevel(before[name])
        logger.handlers = []
        logger.propagate = True


# ---------------------------------------------------------------------------
# set_correlation_id / get_correlation_id
# ---------------------------------------------------------------------------


def test_set_correlation_id_generates_a_uuid_when_not_given():
    cid = set_correlation_id()

    # Round-trips through uuid.UUID, so the generated value really is a UUID.
    assert uuid.UUID(cid) is not None
    assert get_correlation_id() == cid
    assert correlation_id.get() == cid


def test_set_correlation_id_returns_and_stores_the_supplied_value():
    assert set_correlation_id("corr-42") == "corr-42"
    assert get_correlation_id() == "corr-42"
    assert correlation_id.get() == "corr-42"


def test_correlation_id_can_be_overwritten():
    set_correlation_id("first")
    set_correlation_id("second")

    assert get_correlation_id() == "second"


def test_generated_correlation_ids_are_unique():
    assert set_correlation_id() != set_correlation_id()


# ---------------------------------------------------------------------------
# set_request_id / get_request_id
# ---------------------------------------------------------------------------


def test_set_request_id_generates_a_uuid_when_not_given():
    rid = set_request_id()

    assert uuid.UUID(rid) is not None
    assert get_request_id() == rid
    assert request_id.get() == rid


def test_set_request_id_returns_and_stores_the_supplied_value():
    assert set_request_id("req-7") == "req-7"
    assert get_request_id() == "req-7"
    assert request_id.get() == "req-7"


def test_request_and_correlation_ids_are_independent():
    set_correlation_id("corr-x")
    set_request_id("req-x")

    assert get_correlation_id() == "corr-x"
    assert get_request_id() == "req-x"


# ---------------------------------------------------------------------------
# ContextFilter
# ---------------------------------------------------------------------------


def test_context_filter_injects_both_ids_onto_the_record():
    set_correlation_id("corr-f")
    set_request_id("req-f")
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    assert ContextFilter().filter(record) is True
    assert record.correlation_id == "corr-f"
    assert record.request_id == "req-f"


# ---------------------------------------------------------------------------
# setup_logging - real installed configuration
# ---------------------------------------------------------------------------


def test_setup_logging_wires_the_json_console_handler():
    setup_logging()

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.StreamHandler)
    assert handlers[0].level == logging.INFO
    assert isinstance(handlers[0].formatter, jsonlogger.JsonFormatter)
    assert any(isinstance(f, ContextFilter) for f in handlers[0].filters)


def test_setup_logging_honours_the_requested_level():
    setup_logging("DEBUG")

    root = logging.getLogger()
    handler = root.handlers[0]
    assert handler.level == logging.DEBUG
    assert root.level == logging.DEBUG
    assert logging.getLogger("app").level == logging.DEBUG


def test_setup_logging_keeps_chatty_libraries_quiet():
    setup_logging("INFO")

    for name in ("sqlalchemy", "aiokafka"):
        logger = logging.getLogger(name)
        assert logger.level == logging.WARNING
        assert logger.propagate is False
        assert len(logger.handlers) == 1


def test_app_logger_does_not_propagate():
    setup_logging()

    assert logging.getLogger("app").propagate is False


def test_context_filter_runs_before_the_json_formatter_formats_the_line():
    """The JSON formatter requires correlation_id/request_id: end-to-end proof."""
    import io

    setup_logging("INFO")
    stream = io.StringIO()
    handler = logging.getLogger().handlers[0]
    handler.setStream(stream)

    set_correlation_id("corr-json")
    set_request_id("req-json")
    logging.getLogger("app.core.logging.test").info("structured message")

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "structured message"
    assert payload["correlation_id"] == "corr-json"
    assert payload["request_id"] == "req-json"
    assert payload["name"].startswith("app.core.logging.test")


def test_known_defect_json_lines_carry_a_null_level_and_timestamp():
    """Characterisation test for a reported defect (NOT an assertion of intent).

    `app/core/logging.py:58` configures the JSON formatter with
    ``%(timestamp)s`` and ``%(level)s``, neither of which exists on a
    ``logging.LogRecord``. The installed python-json-logger (4.x) resolves them
    to ``None``, so every structured log line in this service is emitted with
    ``"level": null`` and ``"timestamp": null``. Log-level based alerting and
    ordering by timestamp therefore cannot work on user-service JSON logs.

    This test pins the current (broken) output so the defect is visible; it is
    expected to be replaced when production code is fixed.
    """
    import io

    setup_logging("INFO")
    stream = io.StringIO()
    logging.getLogger().handlers[0].setStream(stream)
    set_correlation_id("c")
    set_request_id("r")

    logging.getLogger("app.core.logging.test").warning("level check")

    payload = json.loads(stream.getvalue().strip())
    assert payload["level"] is None
    assert payload["timestamp"] is None
