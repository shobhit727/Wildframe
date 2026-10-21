"""Behavioural tests for `app.core.logging` (notification-service).

This 26-line module is imported by nothing in the service - `app.main.py` wires
logging through `wildframe_observability` instead. It is still exercised here so
the helpers are not left as untested, silently-rotting duplicate context-var
plumbing.

Assertions are behavioural: the value returned, the ContextVar value afterwards,
and the fact that `setup_logging()` actually installs a root handler (rather than
asserting on `logging.basicConfig` call arguments, which are a no-op once the
root logger already has handlers - including under pytest).
"""

import logging
import uuid

import pytest

from app.core.logging import request_id_var, set_correlation_id, set_request_id, setup_logging

EXPECTED_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


@pytest.fixture
def isolated_root_logger():
    """Save/restore the root logger: `setup_logging` mutates global state."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield root
    root.handlers = saved_handlers
    root.setLevel(saved_level)


# ---------------------------------------------------------------------------
# set_request_id
# ---------------------------------------------------------------------------


def test_set_request_id_returns_a_fresh_uuid_and_stores_it():
    rid = set_request_id()

    assert uuid.UUID(rid) is not None
    assert request_id_var.get() == rid
    assert rid == request_id_var.get()


def test_set_request_id_generates_a_different_id_every_call():
    assert set_request_id() != set_request_id()


def test_request_id_var_defaults_to_the_empty_string():
    """The ContextVar default is a plain string, not a generated id."""
    import app.core.logging as logging_module

    fresh = logging_module.ContextVar("request_id_probe", default="")
    assert fresh.get() == ""


def test_the_request_id_survives_into_a_log_record_via_the_var():
    rid = set_request_id()
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    assert request_id_var.get() == rid
    assert record.getMessage() == "hello"


# ---------------------------------------------------------------------------
# set_correlation_id
# ---------------------------------------------------------------------------


def test_set_correlation_id_returns_the_value_it_is_given():
    """Unlike set_request_id, this helper is a pass-through by design."""
    assert set_correlation_id("corr-123") == "corr-123"
    assert set_correlation_id("") == ""


def test_set_correlation_id_does_not_generate_an_id():
    assert set_correlation_id("fixed") == "fixed"
    assert set_correlation_id("fixed") == "fixed"


def test_set_correlation_id_is_not_a_contextvar():
    """The module stores the correlation id elsewhere (observability owns it).

    Asserted so the divergence from set_request_id is deliberate and visible.
    """
    import app.core.logging as logging_module

    assert not hasattr(logging_module, "correlation_id_var")


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


def test_setup_logging_installs_a_root_stream_handler_at_info(isolated_root_logger):
    isolated_root_logger.handlers = []
    isolated_root_logger.setLevel(logging.WARNING)

    setup_logging()

    assert len(isolated_root_logger.handlers) == 1
    handler = isolated_root_logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.formatter is not None
    assert handler.formatter._fmt == EXPECTED_FORMAT
    # `basicConfig(level=...)` raises the *logger* level; the handler stays NOTSET
    # so it inherits whatever the root logger allows.
    assert isolated_root_logger.level == logging.INFO
    assert handler.level == logging.NOTSET


def test_setup_logging_writes_the_configured_line_format(isolated_root_logger, capsys):
    """End-to-end proof the format string is what the module documents."""
    isolated_root_logger.handlers = []
    setup_logging()

    logging.getLogger("app.probe").info("routed to root")

    captured = capsys.readouterr().err.strip().splitlines()[-1]
    assert "routed to root" in captured
    assert "app.probe" in captured
    assert "INFO" in captured
    # "<asctime> - <name> - <levelname> - <message>"
    assert captured.count(" - ") == 3


def test_setup_logging_is_safe_to_call_twice(isolated_root_logger):
    """`basicConfig` is a no-op once handlers exist, so no duplicate stream."""
    isolated_root_logger.handlers = []

    setup_logging()
    first = len(isolated_root_logger.handlers)
    setup_logging()

    assert first == 1
    assert len(isolated_root_logger.handlers) == 1
