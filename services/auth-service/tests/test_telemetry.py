"""Behavioural tests for ``app.telemetry`` — OpenTelemetry/Jaeger tracing setup.

``setup_tracing()`` is gated on ``settings.JAEGER_ENABLED``; this module covers
the disabled short-circuit, the fully-instrumented happy path, and the
``except`` branch that keeps a broken exporter from crashing app startup.
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
# 0.41b0 still imports it at module scope
# (opentelemetry/instrumentation/dependencies.py:4), which makes
# `app.telemetry` un-importable. The root conftest installs this stub, but the
# service's own `pytest.ini` makes `services/auth-service` the rootdir, so the
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
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider

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
    """Turn Jaeger tracing on with an explicit agent endpoint."""
    monkeypatch.setattr(settings, "JAEGER_ENABLED", True)
    monkeypatch.setattr(settings, "JAEGER_AGENT_HOST", "jaeger-agent")
    monkeypatch.setattr(settings, "JAEGER_AGENT_PORT", 6831)
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


class _ClassLevelInstrumentor:
    """Stand-in for an OTel instrumentor used as ``X.instrument()``.

    ``app/telemetry/__init__.py:38`` calls ``FastAPIInstrumentor.instrument()``
    on the *class*, which is broken against the installed
    opentelemetry-instrumentation 0.41b0 (see
    ``test_instrumenting_fastapi_raises_and_is_swallowed``). This shim supplies
    the class-level entry point so the happy-path test can reach the
    SQLAlchemy/Redis instrumentors on the following two lines without patching
    FastAPI globally for the rest of the suite.
    """

    calls = []

    @classmethod
    def instrument(cls, *args, **kwargs):
        cls.calls.append((args, kwargs))


@pytest.fixture
def class_instrumentor():
    _ClassLevelInstrumentor.calls = []
    yield _ClassLevelInstrumentor
    _ClassLevelInstrumentor.calls = []


def test_telemetry_dummy():
    """Retained from the placeholder suite; now asserts real module wiring."""
    assert callable(setup_tracing)
    assert telemetry.settings is settings


def test_telemetry_health():
    """Retained from the placeholder suite; now asserts the real gate default."""
    assert settings.SERVICE_NAME == "auth-service"
    assert settings.JAEGER_ENABLED is False
    assert settings.JAEGER_AGENT_PORT == 6831


class TestSetupTracingDisabled:
    def test_returns_without_side_effects_when_jaeger_disabled(self, caplog, clean_tracer_provider):
        with caplog.at_level(logging.DEBUG, logger="app.telemetry"):
            assert setup_tracing() is None

        assert "Jaeger tracing disabled" in caplog.text

    def test_does_not_install_a_tracer_provider(self, clean_tracer_provider):
        setup_tracing()

        # No SDK provider was installed — the proxy/no-op provider is in place.
        assert not isinstance(trace.get_tracer_provider(), SdkTracerProvider)

    def test_does_not_construct_an_exporter(self, monkeypatch, clean_tracer_provider):
        exporter = MagicMock(side_effect=AssertionError("exporter must not be built"))
        monkeypatch.setattr(telemetry, "JaegerExporter", exporter)

        setup_tracing()

        exporter.assert_not_called()

    def test_does_not_instrument_any_library(self, monkeypatch, clean_tracer_provider):
        fastapi_inst = MagicMock()
        monkeypatch.setattr(telemetry, "FastAPIInstrumentor", fastapi_inst)

        setup_tracing()

        fastapi_inst.instrument.assert_not_called()


class TestSetupTracingEnabled:
    def test_builds_exporter_from_settings_and_registers_provider(
        self, jaeger_enabled, clean_tracer_provider, class_instrumentor
    ):
        exporter = MagicMock()

        with _swap(telemetry, "JaegerExporter", exporter), _swap(
            telemetry, "FastAPIInstrumentor", class_instrumentor
        ):
            setup_tracing()

        exporter.assert_called_once_with(
            agent_host_name="jaeger-agent",
            agent_port=6831,
        )
        assert isinstance(trace.get_tracer_provider(), SdkTracerProvider)
        assert len(class_instrumentor.calls) == 1

    def test_instruments_sqlalchemy_and_redis(
        self, jaeger_enabled, clean_tracer_provider, class_instrumentor
    ):
        sql = _InstanceInstrumentor()
        redis = _InstanceInstrumentor()

        with _swap(telemetry, "JaegerExporter", MagicMock()), _swap(
            telemetry, "FastAPIInstrumentor", class_instrumentor
        ), _swap(telemetry, "SQLAlchemyInstrumentor", lambda: sql), _swap(
            telemetry, "RedisInstrumentor", lambda: redis
        ):
            setup_tracing()

        assert sql.instrumented is True
        assert redis.instrumented is True

    def test_logs_the_configured_agent_endpoint(
        self, jaeger_enabled, clean_tracer_provider, caplog, class_instrumentor
    ):
        with _swap(telemetry, "JaegerExporter", MagicMock()), _swap(
            telemetry, "FastAPIInstrumentor", class_instrumentor
        ), caplog.at_level(logging.DEBUG, logger="app.telemetry"):
            setup_tracing()

        assert "Jaeger tracing initialized (jaeger-agent:6831)" in caplog.text

    def test_adds_a_batch_span_processor_to_the_provider(
        self, jaeger_enabled, clean_tracer_provider, class_instrumentor
    ):
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        with _swap(telemetry, "JaegerExporter", MagicMock()), _swap(
            telemetry, "FastAPIInstrumentor", class_instrumentor
        ):
            setup_tracing()
            provider = trace.get_tracer_provider()

        assert any(
            isinstance(p, BatchSpanProcessor)
            for p in provider._active_span_processor._span_processors
        )

    def test_tolerates_a_non_numeric_agent_port(
        self, jaeger_enabled, clean_tracer_provider, caplog, class_instrumentor
    ):
        """A string port is not validated eagerly — the Jaeger agent client
        only stores the ``(host, port)`` address tuple, so setup must succeed
        and log the endpoint verbatim."""
        jaeger_enabled.JAEGER_AGENT_PORT = "not-a-port"

        with _swap(telemetry, "JaegerExporter", MagicMock()), _swap(
            telemetry, "FastAPIInstrumentor", class_instrumentor
        ), caplog.at_level(logging.DEBUG, logger="app.telemetry"):
            setup_tracing()

        assert "Failed to setup Jaeger tracing" not in caplog.text
        assert "Jaeger tracing initialized (jaeger-agent:not-a-port)" in caplog.text


class TestSetupTracingFailure:
    def test_exporter_failure_is_logged_and_swallowed(
        self, jaeger_enabled, clean_tracer_provider, caplog
    ):
        boom = ValueError("jaeger agent unreachable")

        with _swap(
            telemetry, "JaegerExporter", MagicMock(side_effect=boom)
        ), caplog.at_level(logging.DEBUG, logger="app.telemetry"):
            assert setup_tracing() is None

        assert "Failed to setup Jaeger tracing: jaeger agent unreachable" in caplog.text
        assert not isinstance(trace.get_tracer_provider(), SdkTracerProvider)

    def test_span_processor_failure_is_logged_and_swallowed(
        self, jaeger_enabled, clean_tracer_provider, caplog
    ):
        with _swap(telemetry, "JaegerExporter", MagicMock()), _swap(
            telemetry,
            "BatchSpanProcessor",
            MagicMock(side_effect=RuntimeError("exporter rejected")),
        ), caplog.at_level(logging.DEBUG, logger="app.telemetry"):
            setup_tracing()

        assert "Failed to setup Jaeger tracing: exporter rejected" in caplog.text

    def test_instrumentor_failure_is_logged_and_swallowed(
        self, jaeger_enabled, clean_tracer_provider, caplog
    ):
        # The shipped code calls `FastAPIInstrumentor.instrument()` as a
        # class-level call, so the raising double must expose `.instrument`.
        instrumentor = MagicMock()
        instrumentor.instrument.side_effect = RuntimeError("instrumentation conflict")

        with _swap(telemetry, "JaegerExporter", MagicMock()), _swap(
            telemetry, "FastAPIInstrumentor", instrumentor
        ), caplog.at_level(logging.DEBUG, logger="app.telemetry"):
            setup_tracing()

        assert "Failed to setup Jaeger tracing: instrumentation conflict" in caplog.text

    def test_instrumenting_fastapi_raises_and_is_swallowed(
        self, jaeger_enabled, clean_tracer_provider, caplog
    ):
        """Documents the real-world behaviour of the shipped code.

        ``FastAPIInstrumentor.instrument()`` is called on the *class* at
        app/telemetry/__init__.py:38. In opentelemetry-instrumentation 0.41b0
        ``BaseInstrumentor.instrument`` is an instance method, so that call
        raises ``TypeError`` and the surrounding ``except`` swallows it — the
        SQLAlchemy/Redis instrumentors on the next two lines never run and the
        "initialized" log line is never emitted.
        """
        with _swap(telemetry, "JaegerExporter", MagicMock()), caplog.at_level(
            logging.ERROR, logger="app.telemetry"
        ):
            assert setup_tracing() is None

        assert "Failed to setup Jaeger tracing:" in caplog.text
        assert "Jaeger tracing initialized" not in caplog.text
        # The provider was registered before the failure, so spans still work.
        assert isinstance(trace.get_tracer_provider(), SdkTracerProvider)


def _patch_exporter(replacement):
    """Return a context manager that swaps ``app.telemetry.JaegerExporter``."""
    return _swap(telemetry, "JaegerExporter", replacement)


@contextlib.contextmanager
def _swap(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)
