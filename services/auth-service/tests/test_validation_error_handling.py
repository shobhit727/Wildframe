"""Regression tests for the request-validation error handler.

The handler exists to turn a ``RequestValidationError`` into a clean 422. It
must never itself raise, and it must never emit a non-JSON body.

The specific defect these cover: ``_serializable_errors()`` sanitised
``error["ctx"]`` but not ``error["input"]``. When a client sends a body that
Pydantic cannot parse into the expected model, ``input`` holds the raw request
body, ``json.dumps`` raises ``TypeError`` on the bytes, and the client receives
a 500 instead of the 422 it should have received.

Reachable from outside: any client that posts a JSON body without
``Content-Type: application/json`` hits it.
"""

import json

import pytest
from app.core.database import DatabaseManager
from app.main import create_app
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

VALID_REGISTRATION = {
    "email": "handler-regression@example.com",
    "password": "Zq7!vR2m#xL9pTn4W",
    "first_name": "Handler",
    "last_name": "Regression",
}


@pytest.fixture
async def test_app(tmp_path):
    """Create a test FastAPI app backed by a temp-file SQLite database."""
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/test.db",
        connect_args={"timeout": 15},
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original_get_engine = DatabaseManager.get_engine
    DatabaseManager.get_engine = lambda: test_engine

    original_session_factory = DatabaseManager.get_session_factory
    DatabaseManager._session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    DatabaseManager.get_session_factory = lambda: DatabaseManager._session_factory

    app = create_app()

    yield app

    await test_engine.dispose()
    DatabaseManager.get_engine = original_get_engine
    DatabaseManager.get_session_factory = original_session_factory


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


class TestValidationErrorHandler:
    """The handler must degrade to 422, never to 500."""

    def test_json_body_without_json_content_type_is_422(self, client):
        """A JSON body sent as text/plain must be a 422, not a 500.

        This is the reproduction. Before the fix, ``error["input"]`` held the
        raw body bytes, ``JSONResponse`` raised ``TypeError: Object of type
        bytes is not JSON serializable``, and the response was a 500.
        """
        response = client.post(
            "/api/v1/auth/register",
            content=json.dumps(VALID_REGISTRATION).encode(),
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 422, (
            "Malformed request body must be reported as 422; the validation "
            f"handler raised instead and produced {response.status_code}. "
            f"Body: {response.text[:200]}"
        )

    def test_error_response_body_is_valid_json(self, client):
        """The 422 body must be parseable JSON, never a serialization failure."""
        response = client.post(
            "/api/v1/auth/register",
            content=json.dumps(VALID_REGISTRATION).encode(),
            headers={"Content-Type": "text/plain"},
        )

        # Raises if the handler emitted bytes/str reprs or a truncated body.
        payload = response.json()

        assert payload["error"] == "VALIDATION_ERROR"
        assert payload["message"] == "Request validation failed"
        assert isinstance(payload["details"]["errors"], list)
        assert payload["details"]["errors"], "the underlying errors must be visible"

    def test_serialized_errors_contain_no_unserializable_values(self, client):
        """Every leaf in the error detail must survive a JSON round-trip."""
        response = client.post(
            "/api/v1/auth/register",
            content=json.dumps(VALID_REGISTRATION).encode(),
            headers={"Content-Type": "text/plain"},
        )

        errors = response.json()["details"]["errors"]

        def leaves(node):
            if isinstance(node, dict):
                for value in node.values():
                    yield from leaves(value)
            elif isinstance(node, list):
                for value in node:
                    yield from leaves(value)
            else:
                yield node

        for leaf in leaves(errors):
            assert isinstance(
                leaf, (str, int, float, bool, type(None))
            ), f"non-JSON-serializable value reached the response: {leaf!r}"

    def test_missing_body_is_422(self, client):
        """A request with no body at all must also be a clean 422."""
        response = client.post("/api/v1/auth/register", json=None)

        assert response.status_code == 422
        assert response.json()["error"] == "VALIDATION_ERROR"

    def test_wrongly_typed_field_is_422(self, client):
        """A field of the wrong type must stay a 422 (regression guard).

        Pydantic puts the offending value in ``input``. If a caller ever passes
        something exotic there, it must be coerced rather than raised.
        """
        response = client.post(
            "/api/v1/auth/register",
            json={"email": {"nested": "object"}, "password": 12345},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "VALIDATION_ERROR"
