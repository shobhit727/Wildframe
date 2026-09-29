"""Regression tests for the request-validation error handler.

The handler must turn a ``RequestValidationError`` into a clean 422 JSON body.
It must never itself raise, and it must never emit a non-JSON body.

The defect: ``exc.errors()`` was passed straight into ``JSONResponse``. Pydantic
places the offending value in ``input`` -- and when a client sends a body that
cannot be parsed into the expected model, that value is the **raw request
body**, i.e. ``bytes``. ``json.dumps`` then raises ``TypeError`` inside the
handler, so the client gets a 500 and the underlying validation error is lost
to both the client and the logs.

The sibling service auth-service carries ``_serializable_errors()`` for exactly
this (issue #982); this service never got the equivalent.

These drive the registered ``RequestValidationError`` handler directly.

Why not through HTTP: ``create_app()`` currently mounts no ``/api`` routes at
all (the routers in ``app/api/routes/__init__.py`` are not registered -- see the
"never mounted" finding in the audit). A test that POSTs a live route would
404 before ever reaching the handler, and would have to skip, which proves
nothing. The handler is registered on the app, so we invoke that exact
registered callable with the exact error object Pydantic produces, and assert
the observable contract: 422, parseable JSON, and no non-serializable leaf.
"""

import json

import pytest
from fastapi.exceptions import RequestValidationError
from app.main import create_app


@pytest.fixture
def test_app():
    """Build the app so we can grab its registered exception handler."""
    return create_app()


def _handler_for(app):
    """Return the RequestValidationError handler registered on the app."""
    for exc_class, handler in app.exception_handlers.items():
        if exc_class is RequestValidationError:
            return handler
    pytest.fail("no RequestValidationError handler registered on the app")


def _bytes_body_error() -> RequestValidationError:
    """The exact error Pydantic raises for a JSON body sent as text/plain."""
    return RequestValidationError(
        [
            {
                "type": "model_attributes_type",
                "loc": ("body",),
                "msg": "Input should be a valid dictionary or object to extract fields from",
                "input": b'{"bio": null, "user_id": "x"}',
            }
        ]
    )


def _validator_ctx_error() -> RequestValidationError:
    """A field_validator that raised puts a live exception in ``ctx``."""
    return RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", "bio"),
                "msg": "Value error, bio must not be null",
                "input": None,
                "ctx": {"error": ValueError("bio must not be null")},
            }
        ]
    )


def _leaves(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _leaves(value)
    else:
        yield node


async def test_bytes_input_does_not_raise(test_app):
    """The regression: raw bytes in ``input`` must not explode the handler."""
    handler = _handler_for(test_app)
    response = await handler(None, _bytes_body_error())

    assert response.status_code == 422


async def test_error_body_is_valid_json(test_app):
    """The 422 body must be parseable JSON, never a serialization failure."""
    handler = _handler_for(test_app)
    response = await handler(None, _bytes_body_error())

    payload = json.loads(response.body)

    assert payload["error"] == "VALIDATION_ERROR"
    assert payload["message"] == "Request validation failed"
    assert payload["details"]["errors"], "the underlying errors must stay visible"
    assert payload["details"]["errors"][0]["loc"] == ["body"]


async def test_no_unserializable_value_reaches_the_response(test_app):
    """Every leaf in the error detail must survive a JSON round-trip."""
    handler = _handler_for(test_app)
    response = await handler(None, _bytes_body_error())

    for leaf in _leaves(json.loads(response.body)):
        assert isinstance(
            leaf, (str, int, float, bool, type(None))
        ), f"non-JSON-serializable value reached the response: {leaf!r}"


async def test_validator_ctx_exception_is_serialized(test_app):
    """A field_validator's live exception in ``ctx`` must be coerced, not raised."""
    handler = _handler_for(test_app)
    response = await handler(None, _validator_ctx_error())

    assert response.status_code == 422
    payload = json.loads(response.body)
    assert isinstance(payload["details"]["errors"][0]["ctx"]["error"], str)


async def test_plain_validation_error_still_422(test_app):
    """An ordinary well-formed validation error is unchanged by the fix."""
    handler = _handler_for(test_app)
    error = RequestValidationError(
        [{"type": "missing", "loc": ("body", "bio"), "msg": "Field required", "input": {}}]
    )
    response = await handler(None, error)

    assert response.status_code == 422
    assert json.loads(response.body)["details"]["errors"][0]["type"] == "missing"
