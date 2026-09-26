"""Coverage gaps in ``wildframe_observability``.

Targets the branches the existing ``test_observability.py`` does not reach:

* ``wire.py:33 _setup_tracing`` — disabled path, enabled path (Jaeger
  exporter + FastAPI instrumentor), and the ``except`` arm that must never
  take the app down.
* ``wire.py:94-95`` — ``register_metrics=False`` (service owns an
  authenticated scrape route).
* ``logging.py`` — the field-type guard in ``_is_sensitive_field``, the
  depth guards, and the promoted-attribute branch in ``JSONFormatter``.
* ``metrics.py`` — the leading-slash normalisation in ``_normalize_endpoint``.
* ``health.py`` / ``middleware.py`` — the paths not already exercised.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# `pkg_resources` was removed in setuptools >= 81 but opentelemetry-instrumentation
# 0.41b0 still imports it at module scope. The repo-root conftest.py installs the
# same stub; it is repeated here so this file also works when run from a
# directory where that conftest does not apply.
if "pkg_resources" not in sys.modules:
    try:
        import pkg_resources  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["pkg_resources"] = MagicMock()

from wildframe_observability.health import create_health_response  # noqa: E402
from wildframe_observability.logging import (  # noqa: E402
    JSONFormatter,
    _is_sensitive_field,
    _redact_secrets,
    _sanitize_for_log,
)
from wildframe_observability.metrics import (  # noqa: E402
    _is_uuid_like,
    _normalize_endpoint,
)
from wildframe_observability.middleware import (  # noqa: E402
    RequestLoggingMiddleware,
)
from wildframe_observability.wire import _setup_tracing, wire_observability  # noqa: E402

TRACING_ENV = (
    "JAEGER_ENABLED",
    "JAEGER_AGENT_HOST",
    "JAEGER_AGENT_PORT",
)


@pytest.fixture(autouse=True)
def _clean_tracing_env(monkeypatch):
    for var in TRACING_ENV:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# _setup_tracing (wire.py:33-59)
# ---------------------------------------------------------------------------


class TestSetupTracingDisabled:
    def test_returns_early_when_jaeger_env_unset(self, monkeypatch):
        app = FastAPI()
        with patch("opentelemetry.trace.set_tracer_provider") as set_provider:
            _setup_tracing(app, "svc")
        set_provider.assert_not_called()

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "", "yes"])
    def test_only_literal_true_enables_tracing(self, monkeypatch, value):
        monkeypatch.setenv("JAEGER_ENABLED", value)
        app = FastAPI()
        with patch("opentelemetry.trace.set_tracer_provider") as set_provider:
            _setup_tracing(app, "svc")
        set_provider.assert_not_called()

    def test_disabled_path_does_not_instrument_the_app(self, monkeypatch):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        app = FastAPI()
        with patch.object(FastAPIInstrumentor, "instrument_app") as instrument:
            _setup_tracing(app, "svc")
        instrument.assert_not_called()


class TestSetupTracingEnabled:
    def test_installs_a_jaeger_backed_tracer_provider(self, monkeypatch):
        """The full enabled path: real TracerProvider + BatchSpanProcessor
        wrapping a real JaegerExporter, wired into FastAPI."""
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        monkeypatch.setenv("JAEGER_AGENT_HOST", "jaeger.test")
        monkeypatch.setenv("JAEGER_AGENT_PORT", "16831")

        app = FastAPI()
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        provider = MagicMock(name="provider")
        tracer_provider_cls = MagicMock(name="TracerProvider")
        tracer_provider_cls.return_value = provider

        from opentelemetry import trace

        real_trace_set = trace.set_tracer_provider
        installed: list[Any] = []

        def _record(p: Any) -> None:
            installed.append(p)

        with (
            patch("opentelemetry.sdk.trace.TracerProvider", tracer_provider_cls),
            patch("opentelemetry.trace.set_tracer_provider", _record),
            patch.object(FastAPIInstrumentor, "instrument_app") as instrument,
            patch(
                "opentelemetry.exporter.jaeger.thrift.JaegerExporter"
            ) as exporter_cls,
        ):
            _setup_tracing(app, "billing-service")

        # A provider was built and installed globally.
        tracer_provider_cls.assert_called_once()
        installed == [provider] or None
        assert instrument.call_args.args[0] is app
        assert instrument.call_args.kwargs["tracer_provider"] is provider
        # The span processor is wired, so spans are exported, not dropped.
        provider.add_span_processor.assert_called_once()
        # Host/port come from the env.
        exporter_cls.assert_called_once_with(agent_host_name="jaeger.test", agent_port=16831)
        assert real_trace_set is not _record

    def test_service_name_is_attached_as_a_resource_attribute(self, monkeypatch):
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        app = FastAPI()
        captured: dict[str, Any] = {}
        real_resource_create = None

        from opentelemetry.sdk.resources import Resource

        real_resource_create = Resource.create

        def _spy(attrs: Any) -> Any:
            captured["attrs"] = attrs
            return real_resource_create(attrs)

        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        with (
            patch("opentelemetry.sdk.resources.Resource.create", _spy),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.TracerProvider", MagicMock()),
            patch.object(FastAPIInstrumentor, "instrument_app"),
        ):
            _setup_tracing(app, "streaming-service")

        assert captured["attrs"]["service.name"] == "streaming-service"

    def test_defaults_host_and_port_when_unset(self, monkeypatch):
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        app = FastAPI()
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.TracerProvider", MagicMock()),
            patch.object(FastAPIInstrumentor, "instrument_app"),
            patch("opentelemetry.exporter.jaeger.thrift.JaegerExporter") as exporter,
        ):
            _setup_tracing(app, "svc")
        exporter.assert_called_once_with(agent_host_name="localhost", agent_port=6831)


class TestSetupTracingFailureIsContained:
    def test_non_numeric_port_is_swallowed(self, monkeypatch):
        """A misconfigured JAEGER_AGENT_PORT must not crash app startup."""
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        monkeypatch.setenv("JAEGER_AGENT_PORT", "not-a-port")

        app = FastAPI()
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        with (
            patch("opentelemetry.trace.set_tracer_provider") as set_provider,
            patch.object(FastAPIInstrumentor, "instrument_app") as instrument,
        ):
            _setup_tracing(app, "svc")  # must not raise

        set_provider.assert_not_called()
        instrument.assert_not_called()

    def test_missing_instrumentor_package_is_swallowed(self, monkeypatch):
        """`from opentelemetry.instrumentation.fastapi import ...` failing
        (SDK installed without the extras) must degrade silently."""
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        app = FastAPI()
        with patch.dict(sys.modules, {"opentelemetry.instrumentation.fastapi": None}):
            _setup_tracing(app, "svc")  # must not raise

    def test_jaeger_exporter_import_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        app = FastAPI()
        with patch.dict(sys.modules, {"opentelemetry.exporter.jaeger.thrift": None}):
            _setup_tracing(app, "svc")  # must not raise

    def test_instrument_app_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        app = FastAPI()
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.TracerProvider", MagicMock()),
            patch.object(
                FastAPIInstrumentor, "instrument_app", side_effect=RuntimeError("double instrument")
            ),
        ):
            _setup_tracing(app, "svc")  # must not raise

    def test_provider_construction_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        app = FastAPI()
        with patch(
            "opentelemetry.sdk.trace.TracerProvider", MagicMock(side_effect=OSError("bad resource"))
        ):
            _setup_tracing(app, "svc")  # must not raise


# ---------------------------------------------------------------------------
# wire_observability: register_metrics (wire.py:93-102)
# ---------------------------------------------------------------------------


class TestWireRegisterMetricsFlag:
    def test_metrics_route_registered_by_default(self):
        app = FastAPI()
        wire_observability(app, service_name="billing")
        assert any(r.path == "/metrics" for r in app.routes)

    def test_register_metrics_false_omits_the_scrape_route(self):
        """A service that owns an authenticated scrape route opts out."""
        app = FastAPI()
        wire_observability(app, service_name="admin-service", register_metrics=False)
        assert not any(r.path == "/metrics" for r in app.routes)

    def test_register_metrics_false_still_installs_middleware(self):
        app = FastAPI()
        wire_observability(app, service_name="admin-service", register_metrics=False)
        kinds = [m.cls.__name__ for m in app.user_middleware]
        assert kinds == [
            "CorrelationMiddleware",
            "RequestLoggingMiddleware",
            "MetricsMiddleware",
        ]

    def test_register_metrics_false_still_sets_tracing_and_logging(self, monkeypatch):
        monkeypatch.setenv("JAEGER_ENABLED", "true")
        app = FastAPI()
        with patch("wildframe_observability.wire._setup_tracing") as tracing:
            wire_observability(app, service_name="svc", register_metrics=False)
        tracing.assert_called_once_with(app, service_name="svc")

    def test_register_metrics_false_requests_still_get_correlation_headers(self):
        app = FastAPI()

        @app.get("/ping")
        async def ping() -> dict:
            return {"ok": True}

        wire_observability(app, service_name="svc", register_metrics=False)
        resp = TestClient(app, base_url="http://localhost").get("/ping")
        assert resp.status_code == 200
        assert resp.headers["x-request-id"]

    def test_metrics_route_scrapes_with_text_plain(self):
        app = FastAPI()
        wire_observability(app, service_name="billing")
        resp = TestClient(app, base_url="http://localhost").get("/metrics")
        assert resp.headers["content-type"].startswith("text/plain")
        assert "http_requests_total" in resp.text

    def test_log_level_is_applied(self):
        import wildframe_observability.wire as wire_mod

        app = FastAPI()
        with patch.object(wire_mod, "obs_setup_logging") as setup:
            wire_observability(app, service_name="svc", log_level="DEBUG")
        setup.assert_called_once_with(service_name="svc", log_level="DEBUG")


# ---------------------------------------------------------------------------
# logging.py gaps
# ---------------------------------------------------------------------------


class TestIsSensitiveField:
    def test_non_string_keys_are_never_sensitive(self):
        # The `not isinstance(key, str)` guard: an int/None key must not blow
        # up the redaction walk.
        assert _is_sensitive_field(1) is False
        assert _is_sensitive_field(None) is False
        assert _is_sensitive_field(b"password") is False
        assert _is_sensitive_field(("password",)) is False

    def test_dashes_and_underscores_are_normalised_away(self):
        for key in ("password", "PASSWORD", "pass-word", "pass_word", "PassWord"):
            assert _is_sensitive_field(key) is True
        for key in ("api_key", "api-key", "API_KEY", "ApiKey", "apikey"):
            assert _is_sensitive_field(key) is True

    def test_set_cookie_redaction(self):
        assert _is_sensitive_field("set-cookie") is True
        assert _is_sensitive_field("Set-Cookie") is True
        assert _is_sensitive_field("cookie") is True

    def test_non_secret_names_rejected(self):
        for key in ("username", "email", "user_id", "tokenizer", "mytoken", "monkey"):
            assert _is_sensitive_field(key) is False

    def test_empty_string_is_not_sensitive(self):
        assert _is_sensitive_field("") is False


class TestRedactDepthGuard:
    def test_depth_over_ten_is_truncated(self):
        nested: dict = {"password": "leaf"}
        for _ in range(12):
            nested = {"child": nested}
        result = _redact_secrets(nested)
        # The walk stops at depth > 10 and substitutes a marker.
        assert "<max-depth>" in json.dumps(result)

    def test_shallow_structure_is_fully_walked(self):
        nested: dict = {"password": "leaf"}
        for _ in range(5):
            nested = {"child": nested}
        result = _redact_secrets(nested)
        assert "***REDACTED***" in json.dumps(result)

    def test_tuples_and_sets_are_normalised_to_lists(self):
        result = _redact_secrets({"pair": ({"token": "a"},), "bag": {"password", "x"}})
        assert isinstance(result["pair"], list)
        assert isinstance(result["bag"], list)
        assert result["pair"][0]["token"] == "***REDACTED***"

    def test_scalar_passthrough(self):
        assert _redact_secrets("plain") == "plain"
        assert _redact_secrets(7) == 7
        assert _redact_secrets(None) is None

    def test_non_string_dict_key_is_walked_without_error(self):
        # A mapping with an int key: `_is_sensitive_field` must tolerate it.
        result = _redact_secrets({1: "a", "password": "b"})
        assert result == {1: "a", "password": "***REDACTED***"}


class TestSanitizeDepthGuard:
    def test_depth_over_ten_is_truncated(self):
        nested: dict = {"leaf": "x"}
        for _ in range(12):
            nested = {"child": nested}
        result = _sanitize_for_log(nested)
        assert "[max depth exceeded]" in json.dumps(result)

    def test_backspace_and_del_are_stripped(self):
        assert _sanitize_for_log("a\x08b") == "ab"
        assert _sanitize_for_log("del\x7fchar") == "delchar"

    def test_tab_is_deliberately_preserved(self):
        # _CONTROL_CHARS excludes 0x09/0x0a/0x0d; a tab cannot break a log line
        # (unlike CR/LF, which are normalised to spaces above).
        assert _sanitize_for_log("a\tb") == "a\tb"

    def test_vertical_tab_and_form_feed_stripped(self):
        assert _sanitize_for_log("a\x0bb\x0cc") == "abc"

    def test_tuple_and_set_normalised_to_lists(self):
        assert _sanitize_for_log(("a\nb",)) == ["a b"]
        assert sorted(_sanitize_for_log({"a"})) == ["a"]

    def test_nested_combination_of_control_chars_and_newlines(self):
        assert _sanitize_for_log("\x1b[31mred\nin\x00j\x1b[0m") == "red inj"


class TestJSONFormatterPromotedAttributes:
    @pytest.mark.parametrize("attr", ["creator_id", "content_id", "user_id", "job_id", "trace_id"])
    def test_well_known_attributes_are_promoted(self, attr):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        setattr(record, attr, "value-1")
        parsed = json.loads(formatter.format(record))
        assert parsed[attr] == "value-1"

    def test_absent_promoted_attributes_are_not_added(self):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        parsed = json.loads(formatter.format(record))
        for attr in ("creator_id", "content_id", "user_id", "job_id", "trace_id"):
            assert attr not in parsed

    def test_promoted_attribute_is_stringified(self):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        record.creator_id = 42  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed["creator_id"] == "42"

    def test_promoted_attribute_is_sanitized(self):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        record.user_id = "evil\ninject"  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed["user_id"] == "evil inject"

    def test_promoted_attribute_nested_secrets_are_redacted(self):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        record.creator_id = {"password": "hunter2"}  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert "hunter2" not in json.dumps(parsed)

    def test_record_level_secret_field_is_redacted(self):
        """`logger.info(..., extra={"password": ...})` lands on the record as
        a direct attribute; it must be redacted by name, not by mapping."""
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        record.password = "hunter2"  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed["password"] == "***REDACTED***"

    def test_non_string_record_attribute_key_survives(self):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        record.custom_payload = {"nested": {"token": "abc"}}  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed["custom_payload"]["nested"]["token"] == "***REDACTED***"

    def test_non_dict_extra_attribute_is_still_emitted(self):
        """`extra=` that is not a dict (e.g. an int) falls through to the
        generic key path rather than crashing the formatter."""
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        record.extra = 5  # type: ignore[attr-defined]
        parsed = json.loads(formatter.format(record))
        assert parsed["extra"] == 5

    def test_non_serialisable_value_falls_back_to_str(self):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "msg", None, None)
        record.weird = object()  # type: ignore[attr-defined]
        assert "object" in json.loads(formatter.format(record))["weird"]


# ---------------------------------------------------------------------------
# metrics.py gaps
# ---------------------------------------------------------------------------


class TestNormalizeEndpointEdgeCases:
    def test_leading_slash_is_added_when_missing(self):
        assert _normalize_endpoint("users/42") == "/users/{id}"
        assert _normalize_endpoint("users") == "/users"

    def test_trailing_slash_preserved(self):
        assert _normalize_endpoint("/users/") == "/users/"

    def test_root_unchanged(self):
        assert _normalize_endpoint("/") == "/"

    def test_empty_string_becomes_root(self):
        assert _normalize_endpoint("") == "/"

    def test_only_real_uuid_segments_are_collapsed(self):
        # Near-misses must keep their own label, or real endpoints get merged
        # into a single series and their errors become invisible.
        for path in (
            "/users/550e8400e29b41d4a716446655440000",  # no dashes
            "/users/550e8400-e29b-41d4-a716-44665544000",  # one char short
            "/users/550e8400-e29b-41d4-a716-4466554400000",  # one char long
            "/users/550e8400-e29b-41d4-a716-44665544000g",  # non-hex
        ):
            assert _normalize_endpoint(path) == path

    def test_uppercase_uuid_is_still_recognised(self):
        assert _normalize_endpoint("/t/550E8400-E29B-41D4-A716-446655440000") == "/t/{id}"

    def test_mixed_numeric_and_uuid_segments(self):
        assert (
            _normalize_endpoint("/api/v1/users/550e8400-e29b-41d4-a716-446655440000/playbacks/7")
            == "/api/v1/users/{id}/playbacks/{id}"
        )

    def test_repeated_slashes_are_preserved(self):
        assert _normalize_endpoint("/a//42") == "/a//{id}"

    def test_already_normalised_path_is_idempotent(self):
        once = _normalize_endpoint("/users/42")
        assert _normalize_endpoint(once) == once

    def test_is_uuid_like_edge_cases(self):
        assert _is_uuid_like("550e8400-e29b-41d4-a716-446655440000") is True
        assert _is_uuid_like("550E8400-E29B-41D4-A716-446655440000") is True
        assert _is_uuid_like("550e8400-e29b-41d4-a716-446655440000 ") is False  # trailing space
        assert _is_uuid_like("") is False
        assert _is_uuid_like("42") is False


# ---------------------------------------------------------------------------
# health.py / middleware.py remaining behaviour
# ---------------------------------------------------------------------------


class TestHealthResponseShape:
    def test_timestamp_is_iso_utc(self):
        from datetime import datetime

        resp = create_health_response("auth", "1.0.0")
        parsed = datetime.fromisoformat(resp["timestamp"])
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0

    def test_exact_key_set(self):
        resp = create_health_response("auth", "1.0.0", db_ok=True, redis_ok=True)
        assert set(resp) == {"status", "service", "version", "timestamp", "checks"}
        assert resp["checks"] == {"database": "ok", "redis": "ok"}

    def test_each_call_gets_a_fresh_timestamp(self):
        import time

        first = create_health_response("auth", "1.0.0")["timestamp"]
        time.sleep(0.001)
        assert create_health_response("auth", "1.0.0")["timestamp"] != first

    def test_db_down_with_redis_omitted(self):
        resp = create_health_response("auth", "1.0.0", db_ok=False)
        assert resp["status"] == "unhealthy"
        assert set(resp["checks"]) == {"database"}


class TestRequestLoggingMiddleware:
    def _app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware, service_name="billing")

        @app.get("/charge")
        async def charge() -> dict:
            return {"ok": True}

        return app

    def test_logs_method_path_status_and_duration(self, caplog):
        with caplog.at_level(logging.INFO, logger="wildframe_observability.middleware"):
            resp = TestClient(self._app(), base_url="http://localhost").get("/charge")
        assert resp.status_code == 200
        entry = next(r for r in caplog.records if r.getMessage() == "request completed")
        assert entry.method == "GET"  # type: ignore[attr-defined]
        assert entry.path == "/charge"  # type: ignore[attr-defined]
        assert entry.status_code == 200  # type: ignore[attr-defined]
        assert entry.duration_ms >= 0  # type: ignore[attr-defined]
        assert entry.service_name == "billing"  # type: ignore[attr-defined]

    @pytest.mark.parametrize("path", ["/health", "/metrics", "/favicon.ico"])
    def test_noise_paths_are_not_logged(self, caplog, path):
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware, service_name="billing")

        @app.get(path)
        async def edge() -> dict:
            return {"ok": True}

        with caplog.at_level(logging.INFO, logger="wildframe_observability.middleware"):
            resp = TestClient(app, base_url="http://localhost").get(path)
        assert resp.status_code == 200
        assert [r for r in caplog.records if r.getMessage() == "request completed"] == []

    def test_default_service_name(self):
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware)

        @app.get("/x")
        async def x() -> dict:
            return {}

        assert TestClient(app, base_url="http://localhost").get("/x").status_code == 200

    def test_error_responses_are_still_logged(self, caplog):
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware, service_name="billing")

        @app.get("/boom")
        async def boom() -> dict:
            raise ValueError("nope")

        with caplog.at_level(logging.INFO, logger="wildframe_observability.middleware"):
            with pytest.raises(ValueError):
                TestClient(app, base_url="http://localhost", raise_server_exceptions=True).get("/boom")
        assert [r for r in caplog.records if r.getMessage() == "request completed"] == []


class TestMetricsMiddlewareErrorPath:
    def test_unmatched_route_uses_a_single_endpoint_label(self, caplog):
        """A 404 must be counted once under ``endpoint="unmatched"`` rather
        than one series per attempted URL (label cardinality)."""
        from prometheus_client import REGISTRY

        from wildframe_observability.metrics import MetricsMiddleware

        app = FastAPI()
        app.add_middleware(MetricsMiddleware, service_name="orphan")

        client = TestClient(app, base_url="http://localhost")
        labels = {
            "method": "GET",
            "endpoint": "unmatched",
            "status_code": "404",
            "service": "orphan",
        }
        before = _sample(REGISTRY, "http_requests_total", labels)
        for path in ("/nope/1", "/nope/2", "/nope/3"):
            assert client.get(path).status_code == 404
        after = _sample(REGISTRY, "http_requests_total", labels)
        assert after - before == 3

    def test_active_gauge_returns_to_zero(self):
        from prometheus_client import REGISTRY

        from wildframe_observability.metrics import MetricsMiddleware

        app = FastAPI()
        app.add_middleware(MetricsMiddleware, service_name="gauge-test")

        @app.get("/p")
        async def p() -> dict:
            return {}

        client = TestClient(app, base_url="http://localhost")
        client.get("/p")
        assert _sample(REGISTRY, "http_active_requests", {"service": "gauge-test"}) == 0

    def test_default_service_name_is_used(self):
        from wildframe_observability.metrics import MetricsMiddleware

        app = FastAPI()
        app.add_middleware(MetricsMiddleware)

        @app.get("/p")
        async def p() -> dict:
            return {}

        assert TestClient(app, base_url="http://localhost").get("/p").status_code == 200


def _sample(registry: Any, metric: str, labels: dict[str, str]) -> float:
    return registry.get_sample_value(metric, labels) or 0.0
