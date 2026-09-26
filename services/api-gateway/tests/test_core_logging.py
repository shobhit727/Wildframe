"""Behavioural tests for logging setup and log-injection redaction.

Covers ``app/core/logging.py`` (request/correlation id plumbing) plus the
``HeaderRedactionFilter`` / ``install_header_redaction`` pair in
``app/middleware.py``, which is what keeps bearer tokens and cookies out of
the log stream.
"""

import logging
import uuid

import pytest

from app.core.logging import (
    request_id_var,
    set_correlation_id,
    set_request_id,
    setup_logging,
)
from app.middleware import (
    _REDACTED_VALUE,
    HeaderRedactionFilter,
    _sanitize_message,
    install_header_redaction,
)

# --------------------------------------------------------------------------
# app/core/logging.py
# --------------------------------------------------------------------------


def test_setup_logging_installs_an_info_level_formatted_handler():
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers.clear()
        setup_logging()

        assert root.level == logging.INFO
        assert len(root.handlers) == 1
        fmt = root.handlers[0].formatter
        assert fmt._fmt == "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_setup_logging_is_idempotent_and_keeps_existing_handlers():
    """basicConfig must not duplicate handlers when called more than once."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers.clear()
        setup_logging()
        first = list(root.handlers)
        setup_logging()
        assert root.handlers == first
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_set_request_id_stores_a_fresh_uuid_in_the_contextvar():
    previous = request_id_var.get()
    try:
        rid = set_request_id()
        assert request_id_var.get() == rid
        parsed = uuid.UUID(rid)
        assert str(parsed) == rid
        assert parsed.version == 4

        second = set_request_id()
        assert second != rid, "each request must get a distinct id"
        assert request_id_var.get() == second
    finally:
        request_id_var.set(previous)


async def test_request_id_contextvar_is_isolated_between_concurrent_tasks():
    import asyncio

    previous = request_id_var.get()
    try:

        async def child():
            await asyncio.sleep(0)
            rid = set_request_id()
            await asyncio.sleep(0)
            # Each task keeps its own id even while the other one reassigns.
            return rid, request_id_var.get()

        (a_set, a_get), (b_set, b_get) = await asyncio.gather(child(), child())
        assert a_set == a_get
        assert b_set == b_get
        assert a_set != b_set
    finally:
        request_id_var.set(previous)


def test_set_correlation_id_returns_the_supplied_value_unchanged():
    for value in ("abc-123", "", "a" * 200):
        assert set_correlation_id(value) == value


def test_request_id_var_defaults_to_empty_string():
    """A fresh context (no prior set) yields the declared default."""
    import contextvars

    fresh = contextvars.copy_context()

    def _read():
        return request_id_var.get()

    assert fresh.run(_read) == ""


# --------------------------------------------------------------------------
# message sanitisation
# --------------------------------------------------------------------------


def test_sanitize_message_replaces_crlf_injection_vectors():
    sanitized = _sanitize_message("user=alice\r\nX-Admin: true")
    assert "\r" not in sanitized
    assert "\n" not in sanitized
    assert sanitized == "user=alice??X-Admin: true"


def test_sanitize_message_escapes_nul_and_other_control_bytes():
    sanitized = _sanitize_message("a\x00b\x1fc")
    assert "\x00" not in sanitized
    assert "\x1f" not in sanitized
    assert sanitized.count("?") == 2


def test_sanitize_message_keeps_tabs_so_log_stays_readable():
    assert _sanitize_message("col1\tcol2") == "col1\tcol2"


def test_sanitize_message_passes_empty_and_falsy_messages_through():
    assert _sanitize_message("") == ""
    assert _sanitize_message(None) is None


# --------------------------------------------------------------------------
# HeaderRedactionFilter
# --------------------------------------------------------------------------


def _capture(emit, redact=True):
    """Drive a log call through a handler carrying the redaction filter."""
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    if redact:
        handler.addFilter(HeaderRedactionFilter())
    logger = logging.getLogger("gateway.redaction.test")
    saved_handlers, saved_propagate = logger.handlers, logger.propagate
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        emit(logger)
    finally:
        logger.handlers = saved_handlers
        logger.propagate = saved_propagate
    return records


def test_filter_masks_the_authorization_extra_field():
    def emit(logger):
        logger.info("dispatching", extra={"authorization": "Bearer super-secret"})

    record = _capture(emit)[0]
    assert record.getMessage() == "dispatching"
    assert record.authorization == _REDACTED_VALUE
    assert record.__dict__["authorization"] != "Bearer super-secret"


@pytest.mark.parametrize("field", ["authorization", "Authorization", "cookie", "set-cookie"])
def test_filter_masks_every_sensitive_header_name_case_insensitively(field):
    def emit(logger):
        logger.warning("upstream said", extra={field: "raw-secret-value"})

    record = _capture(emit)[0]
    assert getattr(record, field) == _REDACTED_VALUE


def test_filter_interpolates_args_before_redacting_the_message():
    def emit(logger):
        logger.info("token=%s", "Bearer leaked-token")

    record = _capture(emit)[0]
    assert "leaked-token" in record.getMessage()
    assert record.args == ()


def test_filter_sanitizes_injected_newlines_in_the_formatted_message():
    def emit(logger):
        logger.warning("path=%s", "/evil\r\nSet-Cookie: admin=1")

    record = _capture(emit)[0]
    message = record.getMessage()
    assert "\r" not in message and "\n" not in message
    assert message == "path=/evil??Set-Cookie: admin=1"


def test_filter_leaves_underscore_prefixed_attributes_untouched():
    """The ``_``-prefix guard keeps internal bookkeeping out of redaction."""
    record = logging.LogRecord(
        "gateway.test", logging.INFO, __file__, 1, "msg", None, None
    )
    record._authorization = "internal-bookkeeping"

    assert HeaderRedactionFilter().filter(record) is True
    assert record._authorization == "internal-bookkeeping"


def test_filter_always_admits_the_record_even_when_message_is_empty():
    record = logging.LogRecord(
        "gateway.test", logging.INFO, __file__, 1, "", None, None
    )
    assert HeaderRedactionFilter().filter(record) is True
    assert record.getMessage() == ""


def test_filter_does_not_touch_non_string_attributes():
    record = logging.LogRecord(
        "gateway.test", logging.INFO, __file__, 1, "msg", None, None
    )
    record.cookie = {"session": "abc"}

    HeaderRedactionFilter().filter(record)
    # Only str values are redacted; a dict payload is left intact.
    assert record.cookie == {"session": "abc"}


# --------------------------------------------------------------------------
# install_header_redaction
# --------------------------------------------------------------------------


def test_install_header_redaction_attaches_the_filter_to_root_handlers():
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        root.handlers.clear()
        handler = logging.StreamHandler()
        root.addHandler(handler)

        install_header_redaction()

        assert any(isinstance(f, HeaderRedactionFilter) for f in handler.filters)
    finally:
        root.handlers[:] = saved


def test_install_header_redaction_is_idempotent():
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        root.handlers.clear()
        handler = logging.StreamHandler()
        root.addHandler(handler)

        install_header_redaction()
        install_header_redaction()
        install_header_redaction()

        redactions = [f for f in handler.filters if isinstance(f, HeaderRedactionFilter)]
        assert len(redactions) == 1, "the filter must not be stacked on repeat calls"
    finally:
        root.handlers[:] = saved


def test_installed_filter_redacts_a_real_log_call_end_to_end():
    root = logging.getLogger()
    saved = root.handlers[:]
    emitted = []
    try:
        root.handlers.clear()
        handler = logging.StreamHandler()
        handler.emit = lambda record: emitted.append(record.getMessage())
        root.addHandler(handler)
        root.setLevel(logging.INFO)

        install_header_redaction()
        logging.getLogger("gateway.e2e").warning(
            "upstream rejected request", extra={"cookie": "session=abc123"}
        )
    finally:
        root.handlers[:] = saved

    assert emitted == ["upstream rejected request"]
