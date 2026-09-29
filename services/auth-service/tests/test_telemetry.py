"""Behavioural tests for ``app.telemetry`` — OpenTelemetry tracing setup.

``setup_tracing()`` is gated on ``settings.JAEGER_ENABLED`` and exports spans
over OTLP (the Jaeger Thrift exporter was discontinued upstream at 1.21.0 and
is incompatible with SDK 1.43.0; Jaeger accepts OTLP natively since 1.35, so
the same Jaeger instance still receives the spans — issue #978). This module
covers the disabled short-circuit, the fully-instrumented happy path, and the
``except`` branch that keeps a broken exporter from crashing app startup.

All of ``setup_tracing``'s OTel imports are lazy (inside the function body), so
these tests patch the *import source* — ``opentelemetry`` and the
instrumentor/exporter modules — rather than attributes on ``app.telemetry``,
which only exist for the duration of a call.
"""

import contextlib
import logging
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# `pkg_resources` shim (mirrors the repo-root conftest.py).
#
# setuptools >= 81 removed `pkg_resources`, but opentelemetry-instrumentation
# still imports it at module scope in some releases, which would make
# `app.telemetry` un-importable. The root conftest installs this stub, but the
# service's own pytest config makes `services/auth-service` the rootdir, so the
# root conftest is outside confcutdir and never loaded for this run. Installing
# the same stub here keeps this module self-contained; it is a no-op when a real
# `pkg_resources` is present.
# ---------------------------------------------------------------------------
if "pkg_resources" not in sys.modules:
    try:
        import pkg_resources  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["pkg_resources"] = MagicMock()

from app.core.settings import settings

import app.telemetry as telemetry
import opentelemetry.instrumentation.fastapi as fastapi_module
import opentelemetry.instrumentation.redis as redis_module
import opentelemetry.instrumentation.sqlalchemy as sqlalchemy_module
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc import trace_exporter as otlp_module
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from opentelemetry.sdk.trace import export as sdk_export_module

from app.telemetry import setup_tracing


@pytest.fixture
def clean_tracer_provider():
    """Restore the global tracer provider so tests never leak into each other.

    ``trace.set_tracer_provider`` is guarded by a one-shot latch
    (``_TRACER_PROVIDER_SET_ONCE``), and several of these tests legitimately
    install a real SDK TracerProvider. Both the latch and the module global have
    to be rewound for the next test to observe its own provider.
    """
    original_provider = getattr(trace, "_TRACER_PROVIDER", None)
    latch = trace._TRACER_PROVIDER_SET_ONCE
    original_done = latch._done
    trace._TRACER_PROVIDER = None
    latch._done = False
    yield
    trace._TRACER_PROVIDER = original_provider
    latch._done = original_done


@pytest.fixture
def jaeger_enabled(monkeypatch):
    """Turn Jaeger tracing on with an explicit OTLP endpoint."""
    monkeypatch.setattr(settings, "JAEGER_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    return settings


class _InstanceInstrumentor:
    """Stand-in for an OTel instrumentor used as ``X().instrument()``."""

    def __init__(self):
        self.instrumented = False
        self.uninstrumented = False

    def instrument(self, *args, **kwargs):
        self.instrumented = True

    def uninstrument(self, *args, **kwargs):
        self.uninstrumented = True


def test_telemetry_dummy():
    """Retained from the placeholder suite; now asserts real module wiring."""
    assert callable(setup_tracing)
    assert telemetry.settings is settings


def test_telemetry_health():
    """Retained from the placeholder suite; now asserts the real gate default."""
    assert settings.SERVICE_NAME == "auth-service"
    assert settings.JAEGER_ENABLED is False
    assert settings.OTEL_EXPORTER_OTLP_ENDPOINT == "http://jaeger:4317"


class TestSetupTracingDisabled:
    def test_returns_without_side_effects_when_jaeger_disabled(self, caplog, clean_tracer_provider):
        with caplog.at_level(logging.DEBUG, logger="app.telemetry"):
            assert setup_tracing() is None

        assert "Tracing disabled" in caplog.text

    def test_does_not_install_a_tracer_provider(self, clean_tracer_provider):
        setup_tracing()

        # No SDK provider was installed — the proxy/no-op provider is in place.
        assert not isinstance(trace.get_tracer_provider(), SdkTracerProvider)

    def test_does_not_construct_an_exporter(
        self, monkeypatch, clean_tracer_provider, fastapi_instrumentor
    ):
        exporter = MagicMock(side_effect=AssertionError("exporter must not be built"))
        monkeypatch.setattr(otlp_module, "OTLPSpanExporter", exporter)

        with _swap(fastapi_module, "FastAPIInstrumentor", fastapi_instrumentor):
            setup_tracing()

        exporter.assert_not_called()

    def test_does_not_instrument_any_library(self, monkeypatch, clean_tracer_provider):
        fastapi_inst = MagicMock()
        monkeypatch.setattr(fastapi_module, "FastAPIInstrumentor", fastapi_inst)

        setup_tracing()

        fastapi_inst.assert_not_called()


class TestSetupTracingEnabled:
    def test_builds_exporter_and_registers_provider(
        self, jaeger_enabled, clean_tracer_provider, fastapi_instrumentor
    ):
        with (
            _swap(otlp_module, "OTLPSpanExporter", MagicMock()),
            _swap(fastapi_module, "FastAPIInstrumentor", fastapi_instrumentor),
        ):
            setup_tracing()

        assert isinstance(trace.get_tracer_provider(), SdkTracerProvider)
        assert fastapi_instrumentor().instrumented is True

    def test_instruments_sqlalchemy_and_redis(
        self, jaeger_enabled, clean_tracer_provider, fastapi_instrumentor
    ):
        sql = _InstanceInstrumentor()
        redis = _InstanceInstrumentor()

        with (
            _swap(otlp_module, "OTLPSpanExporter", MagicMock()),
            _swap(fastapi_module, "FastAPIInstrumentor", fastapi_instrumentor),
            _swap(sqlalchemy_module, "SQLAlchemyInstrumentor", lambda: sql),
            _swap(redis_module, "RedisInstrumentor", lambda: redis),
        ):
            setup_tracing()

        assert sql.instrumented is True
        assert redis.instrumented is True

    def test_logs_the_configured_endpoint(
        self, jaeger_enabled, clean_tracer_provider, caplog, fastapi_instrumentor
    ):
        with (
            _swap(otlp_module, "OTLPSpanExporter", MagicMock()),
            _swap(fastapi_module, "FastAPIInstrumentor", fastapi_instrumentor),
            caplog.at_level(logging.DEBUG, logger="app.telemetry"),
        ):
            setup_tracing()

        assert "Tracing initialized, exporting spans over OTLP to http://jaeger:4317" in caplog.text

    def test_adds_a_batch_span_processor_to_the_provider(
        self, jaeger_enabled, clean_tracer_provider, fastapi_instrumentor
    ):
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        with (
            _swap(otlp_module, "OTLPSpanExporter", MagicMock()),
            _swap(fastapi_module, "FastAPIInstrumentor", fastapi_instrumentor),
        ):
            setup_tracing()
            provider = trace.get_tracer_provider()

        assert any(
            isinstance(p, BatchSpanProcessor)
            for p in provider._active_span_processor._span_processors
        )

    def test_exporter_reads_the_otlp_endpoint_from_the_environment(
        self, jaeger_enabled, clean_tracer_provider, fastapi_instrumentor, monkeypatch
    ):
        """The exporter is constructed with no endpoint argument and picks up
        ``OTEL_EXPORTER_OTLP_ENDPOINT`` from the environment itself."""
        captured = {}

        def _exporter(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")

        with (
            _swap(otlp_module, "OTLPSpanExporter", _exporter),
            _swap(fastapi_module, "FastAPIInstrumentor", fastapi_instrumentor),
        ):
            setup_tracing()

        assert captured["args"] == ()
        assert "endpoint" not in captured["kwargs"]

    def test_instrumentor_failure_is_logged_and_swallowed(
        self, jaeger_enabled, clean_tracer_provider, caplog
    ):
        """``FastAPIInstrumentor().instrument()`` used to be called on the
        *class*, which raises ``TypeError`` against the current
        opentelemetry-instrumentation and the surrounding ``except`` swallowed
        it. A constructor that raises proves the except branch keeps app
        startup alive."""
        boom = MagicMock(side_effect=RuntimeError("instrumentation conflict"))

        with (
            _swap(otlp_module, "OTLPSpanExporter", MagicMock()),
            _swap(fastapi_module, "FastAPIInstrumentor", boom),
            caplog.at_level(logging.ERROR, logger="app.telemetry"),
        ):
            assert setup_tracing() is None

        assert "Failed to setup tracing" in caplog.text
        assert "Tracing initialized" not in caplog.text
        # The provider was registered before the failure, so spans still work.
        assert isinstance(trace.get_tracer_provider(), SdkTracerProvider)


class TestSetupTracingFailure:
    def test_exporter_failure_is_logged_and_swallowed(
        self, jaeger_enabled, clean_tracer_provider, caplog
    ):
        boom = ValueError("otlp endpoint unreachable")

        with (
            _swap(otlp_module, "OTLPSpanExporter", MagicMock(side_effect=boom)),
            caplog.at_level(logging.DEBUG, logger="app.telemetry"),
        ):
            assert setup_tracing() is None

        assert "Failed to setup tracing" in caplog.text
        assert "otlp endpoint unreachable" in caplog.text
        assert not isinstance(trace.get_tracer_provider(), SdkTracerProvider)

    def test_span_processor_failure_is_logged_and_swallowed(
        self, jaeger_enabled, clean_tracer_provider, caplog
    ):
        with (
            _swap(otlp_module, "OTLPSpanExporter", MagicMock()),
            _swap(
                sdk_export_module,
                "BatchSpanProcessor",
                MagicMock(side_effect=RuntimeError("exporter rejected")),
            ),
            caplog.at_level(logging.DEBUG, logger="app.telemetry"),
        ):
            setup_tracing()

        assert "Failed to setup tracing" in caplog.text
        assert "exporter rejected" in caplog.text

    def test_missing_otel_imports_are_swallowed_and_logged(
        self, jaeger_enabled, clean_tracer_provider, caplog, monkeypatch
    ):
        """A service without the OTel extras must still boot — the lazy imports
        raise ``ModuleNotFoundError`` and the except branch logs instead of
        crashing startup (issue #978)."""
        import builtins

        real_import = builtins.__import__

        def _no_otel(name, *args, **kwargs):
            if name.startswith("opentelemetry"):
                raise ModuleNotFoundError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_otel)

        assert setup_tracing() is None
        assert "Failed to setup tracing" in caplog.text


@pytest.fixture
def fastapi_instrumentor():
    """Stand-in replacing ``FastAPIInstrumentor`` for one call.

    ``setup_tracing`` constructs the instrumentor (``FastAPIInstrumentor()``
    then ``.instrument()``), so the double is a factory returning a tracking
    instance — same shape as the SQLAlchemy/Redis lambdas.
    """
    inst = _InstanceInstrumentor()
    return lambda: inst


@contextlib.contextmanager
def _swap(module, name, value):
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)
