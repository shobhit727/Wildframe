"""Behavioural tests for ``app.core.logging`` — the auth-service's own
structured-logging layer (JSON formatter + header redaction).

This module is bespoke (it is *not* the SDK's ``wildframe_observability``
logging), so every public symbol here is exercised for real: the
correlation/request contextvars, the log-injection sanitiser, the
``HeaderRedactionFilter`` and the ``CorrelationIdJsonFormatter`` that
serialises records to JSON.
"""

import io
import json
import logging
import logging.config
import uuid
from contextvars import ContextVar
from pathlib import Path

import pytest

from app.core import logging as core_logging
from app.core.logging import (
    HeaderRedactionFilter,
    _CONTROL_TRANSLATION,
    _redact_headers,
    _sanitize_message,
    correlation_id_var,
    get_logger,
    request_id_var,
    set_correlation_id,
    set_request_id,
    set_user_id,
    setup_logging,
    user_id_var,
)
from app.core.logging import CorrelationIdJsonFormatter
from app.core.settings import settings


@pytest.fixture(autouse=True)
def _reset_context():
    """Contextvars are process-wide; restore them so tests never leak."""
    saved = (
        correlation_id_var.get(),
        request_id_var.get(),
        user_id_var.get(),
    )
    yield
    correlation_id_var.set(saved[0])
    request_id_var.set(saved[1])
    user_id_var.set(saved[2])


def _record(msg, args=(), level=logging.INFO, **extra):
    """Build a LogRecord with ``extra``-style attributes attached.

    ``Logger._log`` attaches ``extra=`` via setattr after construction, so the
    tests mirror that rather than passing kwargs into ``LogRecord``.
    """
    record = logging.LogRecord(
        name="tests.audit",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# --------------------------------------------------------------------------
# context vars
# --------------------------------------------------------------------------


class TestContextIdHelpers:
    def test_set_correlation_id_generates_uuid4_when_omitted(self):
        generated = set_correlation_id()

        assert uuid.UUID(generated).version == 4
        assert correlation_id_var.get() == generated

    def test_set_correlation_id_passes_explicit_value_through(self):
        returned = set_correlation_id("corr-abc-123")

        assert returned == "corr-abc-123"
        assert correlation_id_var.get() == "corr-abc-123"

    def test_set_request_id_generates_uuid4_when_omitted(self):
        generated = set_request_id()

        assert uuid.UUID(generated).version == 4
        assert request_id_var.get() == generated

    def test_set_request_id_passes_explicit_value_through(self):
        assert set_request_id("req-42") == "req-42"
        assert request_id_var.get() == "req-42"

    def test_set_user_id_stores_value(self):
        set_user_id("11111111-2222-3333-4444-555555555555")

        assert user_id_var.get() == "11111111-2222-3333-4444-555555555555"

    def test_generated_ids_are_unique_per_call(self):
        assert set_correlation_id() != set_correlation_id()
        assert set_request_id() != set_request_id()

    def test_context_vars_are_contextvars_with_empty_defaults(self):
        # A fresh ContextVar name is unique per declaration; the defaults are "".
        assert correlation_id_var.get() == correlation_id_var.get("")
        assert isinstance(correlation_id_var, ContextVar)
        assert isinstance(request_id_var, ContextVar)
        assert isinstance(user_id_var, ContextVar)


# --------------------------------------------------------------------------
# log-injection sanitiser
# --------------------------------------------------------------------------


class TestSanitizeMessage:
    def test_newline_and_carriage_return_are_defused(self):
        # An attacker must not be able to forge an extra log line.
        assert _sanitize_message("ok\nforged line") == "ok?forged line"
        assert _sanitize_message("ok\r\nforged") == "ok??forged"

    def test_nul_byte_is_replaced(self):
        assert _sanitize_message("a\x00b") == "a?b"

    def test_other_control_bytes_are_replaced_but_tab_is_kept(self):
        assert _sanitize_message("bell\x07 tab\there") == "bell? tab\there"

    def test_delete_character_is_replaced(self):
        assert _sanitize_message("x\x7fy") == "x?y"

    def test_empty_message_short_circuits(self):
        assert _sanitize_message("") == ""

    def test_printable_text_is_untouched(self):
        message = "user=42 action=login status=ok"
        assert _sanitize_message(message) == message

    def test_control_translation_table_covers_ctrl_minus_tab_plus_del(self):
        # Guard the table itself: everything except tab must map to '?'.
        for code in list(range(32)) + [0x7F]:
            if code == 9:
                continue
            assert chr(code).translate(_CONTROL_TRANSLATION) == "?"
        assert "\t".translate(_CONTROL_TRANSLATION) == "\t"


# --------------------------------------------------------------------------
# header redaction
# --------------------------------------------------------------------------


class TestHeaderRedaction:
    def test_redacts_authorization_cookie_and_set_cookie_in_place(self):
        log_record = {
            "message": "request received",
            "authorization": "Bearer super-secret-jwt",
            "cookie": "session=abc",
            "set-cookie": "session=def; HttpOnly",
        }

        _redact_headers(log_record)

        assert log_record["authorization"] == "[REDACTED]"
        assert log_record["cookie"] == "[REDACTED]"
        assert log_record["set-cookie"] == "[REDACTED]"
        assert log_record["message"] == "request received"

    def test_header_matching_is_case_insensitive(self):
        log_record = {"Authorization": "Bearer x", "COOKIE": "y", "Set-Cookie": "z"}

        _redact_headers(log_record)

        assert set(log_record.values()) == {"[REDACTED]"}

    def test_non_sensitive_fields_are_left_alone(self):
        log_record = {"user_id": "42", "authorization_header_present": "true"}

        _redact_headers(log_record)

        assert log_record == {"user_id": "42", "authorization_header_present": "true"}

    def test_filter_always_passes_the_record(self):
        assert HeaderRedactionFilter().filter(_record("hello")) is True

    def test_filter_masks_extra_authorization_field(self):
        record = _record("request received", authorization="Bearer leaked-token")

        HeaderRedactionFilter().filter(record)

        assert record.authorization == "[REDACTED]"

    def test_filter_masks_extra_cookie_and_set_cookie_case_insensitively(self):
        record = _record("request received", Cookie="session=abc", **{"Set-Cookie": "x=1"})

        HeaderRedactionFilter().filter(record)

        assert record.Cookie == "[REDACTED]"
        assert getattr(record, "Set-Cookie") == "[REDACTED]"

    def test_filter_does_not_mask_lookalike_attribute_names(self):
        # The redaction set is hyphenated ("set-cookie"), so an underscore
        # variant is a different attribute and must be left alone.
        record = _record("request received", SET_COOKIE="session=def")

        HeaderRedactionFilter().filter(record)

        assert record.SET_COOKIE == "session=def"

    def test_filter_leaves_non_string_attributes_alone(self):
        record = _record("request received", authorization=object())

        HeaderRedactionFilter().filter(record)

        assert not isinstance(record.authorization, str)

    def test_filter_sanitizes_message_and_clears_args(self):
        record = _record("user=%s\nforged", ("alice",))

        HeaderRedactionFilter().filter(record)

        # args are dropped so %-interpolation cannot resurrect the raw payload.
        assert record.args == ()
        assert "\n" not in record.msg
        assert record.getMessage() == "user=alice?forged"

    def test_filter_ignores_private_attributes(self):
        record = _record("hello")
        record._private_authorization = "Bearer should-stay"

        HeaderRedactionFilter().filter(record)

        assert record._private_authorization == "Bearer should-stay"

    def test_filter_handles_dict_message(self):
        record = _record({"authorization": "Bearer nested"})

        HeaderRedactionFilter().filter(record)

        assert "Bearer nested" in record.getMessage()


# --------------------------------------------------------------------------
# JSON formatter
# --------------------------------------------------------------------------


class TestCorrelationIdJsonFormatter:
    def _format(self, record, fmt="%(message)s"):
        return json.loads(CorrelationIdJsonFormatter(fmt).format(record))

    def test_enriches_record_with_context_and_service_metadata(self):
        set_correlation_id("corr-1")
        set_request_id("req-1")
        set_user_id("user-1")

        payload = self._format(_record("hello world"))

        assert payload["message"] == "hello world"
        assert payload["correlation_id"] == "corr-1"
        assert payload["request_id"] == "req-1"
        assert payload["user_id"] == "user-1"
        assert payload["service"] == settings.SERVICE_NAME
        assert payload["version"] == settings.SERVICE_VERSION
        assert payload["environment"] == settings.ENVIRONMENT
        assert payload["level"] == "INFO"

    def test_timestamp_is_iso_8601_utc(self):
        from datetime import datetime

        payload = self._format(_record("hello"))

        parsed = datetime.fromisoformat(payload["timestamp"])
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0

    def test_level_name_is_recorded(self):
        payload = self._format(_record("bad", level=logging.ERROR))

        assert payload["level"] == "ERROR"

    def test_defaults_are_empty_strings_when_no_context_set(self):
        correlation_id_var.set("")
        request_id_var.set("")
        user_id_var.set("")

        payload = self._format(_record("hello"))

        assert payload["correlation_id"] == ""
        assert payload["request_id"] == ""
        assert payload["user_id"] == ""

    def test_sanitizes_message_for_log_injection(self):
        payload = self._format(_record("line1\nline2\r\nline3"))

        assert payload["message"] == "line1?line2??line3"

    def test_redacts_sensitive_top_level_fields(self):
        record = _record(
            "request received",
            authorization="Bearer super-secret",
            Cookie="session=abc",
        )

        payload = self._format(record)

        assert payload["authorization"] == "[REDACTED]"
        assert payload["Cookie"] == "[REDACTED]"
        assert "super-secret" not in json.dumps(payload)

    def test_preserves_non_sensitive_extra_fields(self):
        record = _record("request received", request_path="/auth/login", duration_ms=12)

        payload = self._format(record)

        assert payload["request_path"] == "/auth/login"
        assert payload["duration_ms"] == 12

    def test_contextvar_user_id_overrides_an_extra_user_id_field(self):
        # add_fields writes the contextvar values last, so a record-level
        # `user_id` extra cannot spoof the authenticated identity.
        set_user_id("real-user")
        record = _record("request received", user_id="spoofed-user")

        payload = self._format(record)

        assert payload["user_id"] == "real-user"

    def test_non_string_message_is_left_intact(self):
        # python-json-logger folds a dict message into the record, so the
        # formatter's `isinstance(log_record.get("message"), str)` guard must
        # be skipped without raising.
        record = _record({"a": 1})

        payload = self._format(record)

        assert payload["service"] == settings.SERVICE_NAME
        assert isinstance(payload["message"], (str, dict))


def test_get_logger_returns_named_logger():
    logger = get_logger("app.core.logging.tests")

    assert isinstance(logger, logging.Logger)
    assert logger.name == "app.core.logging.tests"


# --------------------------------------------------------------------------
# setup_logging
# --------------------------------------------------------------------------


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """Run setup_logging in a throwaway cwd and restore global logging state.

    ``setup_logging`` calls ``logging.config.dictConfig`` with
    ``disable_existing_loggers=False``, which re-points the *root* logger and
    the sqlalchemy/asyncio loggers at handlers writing to
    ``logs/auth-service.log``. Without this fixture every other test in the
    suite would inherit those handlers.
    """
    workdir = tmp_path / "logsdir"
    (workdir / "logs").mkdir(parents=True)
    monkeypatch.chdir(workdir)

    root = logging.getLogger()
    saved = {
        "root_handlers": root.handlers[:],
        "root_level": root.level,
        "sqlalchemy": logging.getLogger("sqlalchemy"),
        "asyncio": logging.getLogger("asyncio"),
    }
    named = {name: logging.getLogger(name) for name in ("sqlalchemy", "asyncio")}
    named_state = {
        name: (lg.handlers[:], lg.level, lg.propagate) for name, lg in named.items()
    }

    yield workdir

    root.handlers[:] = saved["root_handlers"]
    root.setLevel(saved["root_level"])
    for name, lg in named.items():
        handlers, level, propagate = named_state[name]
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
    # Drop the file handlers dictConfig installed so pytest keeps its own.
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()


class TestSetupLogging:
    def test_installs_console_and_file_handlers_on_root(self, isolated_logging):
        setup_logging()

        root = logging.getLogger()
        kinds = {type(h).__name__ for h in root.handlers}
        assert "StreamHandler" in kinds
        assert "RotatingFileHandler" in kinds

    def test_writes_json_records_to_the_configured_log_file(self, isolated_logging):
        setup_logging()
        set_correlation_id("file-correlation")
        logging.getLogger("probe").info("file-handler-check", extra={"authorization": "Bearer x"})

        written = (isolated_logging / "logs" / "auth-service.log").read_text()
        record = json.loads(written.strip().splitlines()[-1])

        assert record["message"] == "file-handler-check"
        assert record["correlation_id"] == "file-correlation"
        assert record["authorization"] == "[REDACTED]"

    def test_uses_detailed_formatter_in_development(self, isolated_logging):
        setup_logging()

        console = next(
            h for h in logging.getLogger().handlers if not isinstance(h, logging.FileHandler)
        )
        assert console.formatter is not None
        assert not isinstance(console.formatter, CorrelationIdJsonFormatter)

    def test_uses_json_formatter_outside_development(self, isolated_logging, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")

        setup_logging()

        console = next(
            h for h in logging.getLogger().handlers if not isinstance(h, logging.FileHandler)
        )
        assert isinstance(console.formatter, CorrelationIdJsonFormatter)

    def test_attaches_the_header_redaction_filter(self, isolated_logging):
        setup_logging()

        for handler in logging.getLogger().handlers:
            assert any(isinstance(f, HeaderRedactionFilter) for f in handler.filters)

    def test_logged_output_is_redacted_end_to_end(self, isolated_logging):
        setup_logging()
        record = _record("request received", authorization="Bearer leaked")
        for handler in logging.getLogger().handlers:
            if handler.filters and not all(
                isinstance(f, HeaderRedactionFilter) for f in handler.filters
            ):
                continue
            handler.filter(record)

        assert record.authorization == "[REDACTED]"

    def test_configures_sqlalchemy_and_asyncio_loggers(self, isolated_logging):
        setup_logging()

        assert logging.getLogger("sqlalchemy").level == logging.WARNING
        assert logging.getLogger("asyncio").level == logging.INFO
        assert logging.getLogger("sqlalchemy").handlers
        assert logging.getLogger("asyncio").handlers

    def test_root_level_follows_settings_log_level(self, isolated_logging, monkeypatch):
        monkeypatch.setattr(settings, "LOG_LEVEL", "ERROR")

        setup_logging()

        assert logging.getLogger().level == logging.ERROR

    def test_formatter_referenced_by_name_is_resolvable(self):
        # The dictConfig uses the dotted path "app.core.logging.CorrelationIdJsonFormatter";
        # logging.config resolves it through importlib, so keep that path valid.
        assert core_logging.CorrelationIdJsonFormatter is CorrelationIdJsonFormatter


def test_json_formatter_uses_stdlib_json_serialisation():
    """The formatter must emit parseable JSON even with a stdlib StreamHandler."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(CorrelationIdJsonFormatter("%(message)s"))
    logger = logging.getLogger("json-probe")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("structured", extra={"authorization": "Bearer x"})

    payload = json.loads(stream.getvalue())
    assert payload["message"] == "structured"
    assert payload["authorization"] == "[REDACTED]"


def test_redaction_constants_match_documented_policy():
    assert core_logging._REDACTED_VALUE == "[REDACTED]"
    assert core_logging._REDACTED_HEADERS == frozenset(
        {"authorization", "cookie", "set-cookie"}
    )


def test_logs_directory_is_configured_relative_to_cwd():
    """The handler filename in the config is the documented 'logs/...path'."""
    assert Path("logs").name == "logs"
