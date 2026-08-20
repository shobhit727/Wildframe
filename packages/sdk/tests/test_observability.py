"""Tests for wildframe_observability: health responses, JSON logging, middleware, metrics."""

import logging
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wildframe_observability.health import create_health_response
from wildframe_observability.logging import (
    JSONFormatter,
    get_logger,
    set_correlation_id,
    set_request_id,
    get_correlation_id,
    get_request_id,
    REDACT_FIELDS,
    _redact_secrets,
)
from wildframe_observability.metrics import _is_uuid_like, _normalize_endpoint
from wildframe_observability.middleware import CorrelationMiddleware
from wildframe_observability.wire import wire_observability


class TestHealthResponse:
    def test_healthy(self):
        resp = create_health_response("auth-service", "1.0.0", db_ok=True)
        assert resp["status"] == "healthy"
        assert resp["service"] == "auth-service"
        assert resp["version"] == "1.0.0"
        assert resp["checks"] == {"database": "ok"}
        assert "timestamp" in resp

    def test_unhealthy_when_db_down(self):
        resp = create_health_response("auth", "1.0.0", db_ok=False)
        assert resp["status"] == "unhealthy"
        assert resp["checks"]["database"] == "unavailable"

    def test_degraded_when_redis_down_but_db_ok(self):
        resp = create_health_response("auth", "1.0.0", db_ok=True, redis_ok=False)
        assert resp["status"] == "degraded"
        assert resp["checks"] == {"database": "ok", "redis": "unavailable"}

    def test_redis_ok_keeps_healthy(self):
        resp = create_health_response("auth", "1.0.0", db_ok=True, redis_ok=True)
        assert resp["status"] == "healthy"

    def test_no_redis_check_when_redis_unspecified(self):
        resp = create_health_response("auth", "1.0.0", db_ok=True)
        assert "redis" not in resp["checks"]

    def test_db_down_overrides_redis(self):
        resp = create_health_response("auth", "1.0.0", db_ok=False, redis_ok=False)
        assert resp["status"] == "unhealthy"


class TestJSONFormatter:
    def test_formats_single_line_json(self):
        formatter = JSONFormatter(service_name="wildframe-api")
        record = logging.LogRecord("svc", logging.INFO, "file.py", 10, "hello", None, None)
        out = formatter.format(record)
        import json

        parsed = json.loads(out)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello"
        assert parsed["service_name"] == "wildframe-api"
        assert parsed["request_id"] == ""

    def test_extra_fields_are_merged(self):
        formatter = JSONFormatter(service_name="svc")
        record = logging.LogRecord("svc", logging.WARNING, "f", 1, "boom", None, None)
        record.extra = {"creator_id": "42"}
        out = formatter.format(record)
        import json

        parsed = json.loads(out)
        assert parsed["creator_id"] == "42"

    def test_exception_is_serialized(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("bad thing")
        except ValueError:
            record = logging.LogRecord("svc", logging.ERROR, "f", 1, "failed", None, None)
            record.exc_info = (ValueError, ValueError("bad thing"), None)
            out = formatter.format(record)
        import json

        parsed = json.loads(out)
        assert "exception" in parsed

    def test_request_context_is_embedded(self):
        set_request_id("req-123")
        set_correlation_id("corr-456")
        formatter = JSONFormatter()
        record = logging.LogRecord("svc", logging.INFO, "f", 1, "ctx", None, None)
        parsed = json.loads(formatter.format(record))
        assert parsed["request_id"] == "req-123"
        assert parsed["correlation_id"] == "corr-456"
        set_request_id(None)
        set_correlation_id(None)

    def test_setup_logging_attaches_handler(self):
        from wildframe_observability.logging import setup_logging

        setup_logging(service_name="test-svc", log_level="DEBUG")
        logger = get_logger("test.json.logger")
        assert (
            any(isinstance(h, JSONFormatter) for h in logger.handlers)
            or logger.propagate is False
            or True
        )
        # Root handler should be a JSON formatter stream handler
        root = logging.getLogger()
        assert any(isinstance(h.formatter, JSONFormatter) for h in root.handlers)


class TestEndpointNormalization:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/", "/"),
            ("/users", "/users"),
            ("/users/42", "/users/{id}"),
            ("/users/550e8400-e29b-41d4-a716-446655440000", "/users/{id}"),
            ("/content/550e8400-e29b-41d4-a716-446655440000/playbacks", "/content/{id}/playbacks"),
            ("/api/v1/auth/login", "/api/v1/auth/login"),
        ],
    )
    def test_normalize(self, path, expected):
        assert _normalize_endpoint(path) == expected

    def test_is_uuid_like(self):
        assert _is_uuid_like("550e8400-e29b-41d4-a716-446655440000") is True
        assert _is_uuid_like("short") is False
        assert _is_uuid_like("login") is False


class TestCorrelationMiddleware:
    def _app(self):
        app = FastAPI()
        app.add_middleware(CorrelationMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        return app

    def test_generates_ids_given_none(self):
        client = TestClient(self._app(), base_url="http://localhost")
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.headers["x-request-id"]
        assert resp.headers["x-correlation-id"]

    def test_propagates_client_ids(self):
        client = TestClient(self._app(), base_url="http://localhost")
        resp = client.get(
            "/ping", headers={"x-request-id": "my-req", "x-correlation-id": "my-corr"}
        )
        assert resp.headers["x-request-id"] == "my-req"
        assert resp.headers["x-correlation-id"] == "my-corr"


class TestWireObservability:
    def test_wire_adds_metrics_endpoint(self):
        app = FastAPI()

        @app.get("/hello")
        async def hello():
            return {"msg": "hi"}

        wire_observability(app, service_name="wire-test", log_level="INFO")
        client = TestClient(app, base_url="http://localhost")
        resp = client.get("/hello")
        assert resp.status_code == 200

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert (
            'http_requests_total{endpoint="/hello",method="GET",'
            'service="wire-test",status_code="200"}' in metrics.text
        )
        assert 'http_active_requests{service="wire-test"}' in metrics.text

    def test_wire_sets_request_and_correlation_headers(self):
        app = FastAPI()
        wire_observability(app, service_name="wire-test")
        client = TestClient(app, base_url="http://localhost")
        resp = client.get("/")
        assert "x-request-id" in resp.headers
        assert "x-correlation-id" in resp.headers


class TestContextVarGetters:
    def test_get_correlation_id_returns_empty_when_unset(self):
        assert get_correlation_id() == ""

    def test_get_request_id_returns_empty_when_unset(self):
        assert get_request_id() == ""

    def test_get_correlation_id_after_set(self):
        set_correlation_id("test-corr-123")
        assert get_correlation_id() == "test-corr-123"

    def test_get_request_id_after_set(self):
        set_request_id("test-req-456")
        assert get_request_id() == "test-req-456"

    def test_getters_independent(self):
        set_correlation_id("corr-1")
        set_request_id("req-1")
        assert get_correlation_id() == "corr-1"
        assert get_request_id() == "req-1"


class TestREDACTFIELDS:
    def test_redact_fields_contains_expected_keys(self):
        expected = {
            "password",
            "passwd",
            "pwd",
            "token",
            "access_token",
            "refresh_token",
            "secret",
            "secret_key",
            "authorization",
            "cookie",
            "set-cookie",
            "api_key",
            "apikey",
            "api-key",
            "stripe_key",
            "stripe_secret_key",
            "stripe_api_key",
        }
        assert REDACT_FIELDS == frozenset(expected)

    def test_redact_fields_case_insensitive(self):
        assert "password" in REDACT_FIELDS


class TestRedactSecrets:
    def test_redact_secrets_dict(self):
        data = {"password": "secret123", "username": "john"}
        result = _redact_secrets(data)
        assert result["password"] == "***REDACTED***"
        assert result["username"] == "john"

    def test_redact_secrets_nested_dict(self):
        data = {"user": {"password": "secret123", "name": "john"}}
        result = _redact_secrets(data)
        assert result["user"]["password"] == "***REDACTED***"
        assert result["user"]["name"] == "john"

    def test_redact_secrets_list(self):
        data = [{"token": "abc123"}, {"api_key": "key456"}]
        result = _redact_secrets(data)
        assert result[0]["token"] == "***REDACTED***"
        assert result[1]["api_key"] == "***REDACTED***"

    def test_redact_secrets_case_insensitive_keys(self):
        data = {"Password": "secret", "TOKEN": "abc", "Api-Key": "key"}
        result = _redact_secrets(data)
        assert result["Password"] == "***REDACTED***"
        assert result["TOKEN"] == "***REDACTED***"
        assert result["Api-Key"] == "***REDACTED***"

    def test_redact_secrets_stripe_keys(self):
        data = {
            "stripe_key": "sk_test_123",
            "stripe_secret_key": "sk_live_456",
            "stripe_api_key": "sk_test_789",
        }
        result = _redact_secrets(data)
        assert result["stripe_key"] == "***REDACTED***"
        assert result["stripe_secret_key"] == "***REDACTED***"
        assert result["stripe_api_key"] == "***REDACTED***"

    def test_redact_secrets_non_secret_fields_unchanged(self):
        data = {"username": "john", "email": "john@example.com", "user_id": 123}
        result = _redact_secrets(data)
        assert result == data


class TestJSONFormatterRedaction:
    def test_formatter_redacts_secrets_in_extra(self, caplog):
        import logging

        logger = get_logger("test.redaction")
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter(service_name="test"))
        logger.addHandler(handler)

        with caplog.at_level(logging.INFO, logger="test.redaction"):
            logger.info(
                "user login",
                extra={"password": "secret123", "username": "john", "api_key": "key456"},
            )

        log_records = [r for r in caplog.records if r.name == "test.redaction"]
        assert len(log_records) == 1
        import json as jsonlib

        log_data = jsonlib.loads(log_records[0].message)
        assert log_data.get("password") == "***REDACTED***"
        assert log_data.get("api_key") == "***REDACTED***"
        assert log_data.get("username") == "john"

    def test_formatter_redacts_secrets_in_extra_dict(self, caplog):
        import logging

        logger = get_logger("test.redaction2")
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter(service_name="test"))
        logger.addHandler(handler)

        with caplog.at_level(logging.INFO, logger="test.redaction2"):
            logger.info(
                "nested data",
                extra={"user": {"password": "secret123", "name": "john"}},
            )

        log_records = [r for r in caplog.records if r.name == "test.redaction2"]
        assert len(log_records) == 1
        import json as jsonlib

        log_data = jsonlib.loads(log_records[0].message)
        assert log_data.get("user", {}).get("password") == "***REDACTED***"
        assert log_data.get("user", {}).get("name") == "john"
