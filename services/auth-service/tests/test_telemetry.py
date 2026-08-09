"""Coverage for auth telemetry tracing setup."""

from unittest.mock import MagicMock, patch


from app.telemetry import setup_tracing


def test_setup_noop_when_disabled():
    with patch("app.telemetry.settings", MagicMock(JAEGER_ENABLED=False)):
        setup_tracing()  # should not raise


def test_setup_initializes_when_enabled():
    fake_export = MagicMock()
    fake_provider = MagicMock()
    fake_batch = MagicMock()
    with (
        patch(
            "app.telemetry.settings",
            MagicMock(JAEGER_ENABLED=True, JAEGER_AGENT_HOST="h", JAEGER_AGENT_PORT=6831),
        ),
        patch("app.telemetry.JaegerExporter", return_value=fake_export),
        patch("app.telemetry.TracerProvider", return_value=fake_provider),
        patch("app.telemetry.BatchSpanProcessor", return_value=fake_batch),
        patch("app.telemetry.trace") as fake_trace,
        patch("app.telemetry.FastAPIInstrumentor"),
        patch("app.telemetry.SQLAlchemyInstrumentor"),
        patch("app.telemetry.RedisInstrumentor"),
    ):
        setup_tracing()
        fake_provider.add_span_processor.assert_called_once()
        fake_trace.set_tracer_provider.assert_called_once_with(fake_provider)


def test_setup_swallows_failures():
    with (
        patch("app.telemetry.settings", MagicMock(JAEGER_ENABLED=True)),
        patch("app.telemetry.JaegerExporter", side_effect=RuntimeError("boom")),
    ):
        setup_tracing()  # must not raise
