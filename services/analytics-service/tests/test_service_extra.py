"""Replaces the former placeholder file.

The old contents were::

    def test_analytics_extra():
        assert True

-- a literal with no code under test. This module holds real behavioural
tests for the gaps that file was standing in for:

* ``app/core/content_client.py`` -- the content-service ownership resolver that
  ``require_content_access`` depends on. Its transport/protocol error arms were
  unexercised, and those arms are what make the authorization decision
  fail-closed, so the distinction between "content missing" (None) and
  "cannot tell" (ContentServiceUnavailableError) is asserted precisely.
* ``app/schemas/__init__.py:17`` -- the recursive ``_nesting_depth`` helper's
  empty-collection arms.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import app.core.content_client as content_client
from app.core.content_client import (
    ContentServiceUnavailableError,
    close_content_client,
    get_content_client,
    resolve_content_owner,
)
from app.schemas import MAX_EVENT_DATA_DEPTH, LogEventRequest

# ============================= content_client ================================


@pytest.fixture(autouse=True)
def reset_client():
    """Drop the process-wide client so each test builds a fresh one."""
    content_client._client = None
    yield
    content_client._client = None


def stub_http(response=None, exc=None):
    """Patch the shared client's ``get`` to return/raise a canned result."""
    get = AsyncMock(return_value=response, side_effect=exc)
    client = AsyncMock()
    client.get = get
    content_client._client = client
    return get


def json_response(status_code: int, payload=None):
    return httpx.Response(
        status_code,
        json=payload if payload is not None else {},
        request=httpx.Request("GET", "http://content-service/api/v1/content/x"),
    )


@pytest.mark.unit
def test_get_content_client_is_process_wide_and_bounded():
    """The client is memoised and configured from settings."""
    first = get_content_client()
    second = get_content_client()

    assert first is second
    assert str(first.base_url) == content_client.settings.CONTENT_SERVICE_URL
    assert first.timeout.read == content_client.settings.CONTENT_SERVICE_TIMEOUT_SECONDS


@pytest.mark.unit
async def test_close_content_client_closes_and_clears():
    """Teardown must aclose the client and reset the module global."""
    client = get_content_client()
    content_client._client = client

    await close_content_client()

    assert content_client._client is None
    # A second close is a harmless no-op.
    await close_content_client()
    assert content_client._client is None


@pytest.mark.unit
async def test_close_content_client_is_a_noop_when_never_built():
    """Closing before any request must not raise."""
    assert content_client._client is None
    await close_content_client()
    assert content_client._client is None


@pytest.mark.unit
async def test_resolve_content_owner_returns_the_creator_id():
    """The happy path: content-service's ``creator_id`` is returned as a UUID."""
    owner = uuid4()
    content_id = uuid4()
    get = stub_http(json_response(200, {"creator_id": str(owner)}))

    assert await resolve_content_owner(content_id) == owner
    assert get.await_count == 1
    assert str(content_id) in get.await_args.args[0]


@pytest.mark.unit
async def test_resolve_content_owner_returns_none_for_404():
    """content_client.py:63-64 -- a 404 means "does not exist", not an error."""
    content_id = uuid4()
    stub_http(json_response(404))

    assert await resolve_content_owner(content_id) is None


@pytest.mark.unit
async def test_resolve_content_owner_returns_none_when_creator_id_is_absent():
    """A 200 with no ``creator_id`` is also "unknown", not a crash."""
    stub_http(json_response(200, {"title": "Some Movie"}))

    assert await resolve_content_owner(uuid4()) is None


@pytest.mark.unit
async def test_resolve_content_owner_returns_none_for_a_null_creator_id():
    """An explicit null creator_id must not become a UUID parse error."""
    stub_http(json_response(200, {"creator_id": None}))

    assert await resolve_content_owner(uuid4()) is None


@pytest.mark.unit
async def test_resolve_content_owner_raises_unavailable_on_transport_error(caplog):
    """content_client.py:59-62 -- a transport failure must raise, never return None.

    This is the distinction the authorization depends on: ``None`` means "no
    such content" (deny 404), while raising means "cannot tell" (deny 503).
    Collapsing the two would silently downgrade an outage into a 404.
    """
    content_id = uuid4()
    stub_http(exc=httpx.ConnectError("connection refused"))

    with caplog.at_level("WARNING", logger="app.core.content_client"):
        with pytest.raises(ContentServiceUnavailableError):
            await resolve_content_owner(content_id)

    assert any("content-service unavailable" in r.message for r in caplog.records)


@pytest.mark.unit
async def test_resolve_content_owner_raises_unavailable_on_a_timeout(caplog):
    """A read timeout is a transport error too, so it must also raise."""
    stub_http(exc=httpx.ReadTimeout("timed out"))

    with caplog.at_level("WARNING", logger="app.core.content_client"):
        with pytest.raises(ContentServiceUnavailableError):
            await resolve_content_owner(uuid4())


@pytest.mark.unit
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 502, 503])
async def test_resolve_content_owner_raises_unavailable_on_error_statuses(status, caplog):
    """content_client.py:65-71 -- any non-200/404 is an untrustworthy answer.

    Notably 403 is in this bucket: a gateway denial must not be read as
    "content does not exist".
    """
    stub_http(json_response(status))

    with caplog.at_level("WARNING", logger="app.core.content_client"):
        with pytest.raises(ContentServiceUnavailableError):
            await resolve_content_owner(uuid4())

    assert any("content-service returned" in r.message for r in caplog.records)


@pytest.mark.unit
async def test_resolve_content_owner_raises_unavailable_on_a_malformed_body(caplog):
    """content_client.py:72-79 -- an unparseable payload must raise."""
    bad_json = httpx.Response(
        200,
        content=b"<html>not json</html>",
        request=httpx.Request("GET", "http://content-service/api/v1/content/x"),
    )
    stub_http(bad_json)

    with caplog.at_level("WARNING", logger="app.core.content_client"):
        with pytest.raises(ContentServiceUnavailableError):
            await resolve_content_owner(uuid4())


@pytest.mark.unit
async def test_resolve_content_owner_raises_unavailable_on_a_non_uuid_creator_id():
    """A 200 whose ``creator_id`` is not a UUID must raise, not leak a string."""
    stub_http(json_response(200, {"creator_id": "not-a-uuid"}))

    with pytest.raises(ContentServiceUnavailableError):
        await resolve_content_owner(uuid4())


# Sentinel for "the body is literally JSON null", which the json_response()
# helper cannot express because it substitutes {} for None.
_RAW_NULL = object()


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("just a string", id="json-string"),
        pytest.param(42, id="json-number"),
        pytest.param(["a", "b"], id="json-array"),
        pytest.param(_RAW_NULL, id="json-null"),
    ],
)
async def test_resolve_content_owner_raises_unavailable_on_a_scalar_body(payload):
    """A 200 whose body is valid JSON but not an object must fail closed.

    ``payload = response.json()`` followed by ``payload.get("creator_id")``
    assumed the body was a JSON object. A JSON scalar parses fine and then
    raises ``AttributeError``, which was not in the ``except (ValueError,
    TypeError)`` clause, so it escaped ``resolve_content_owner``.

    That broke the fail-closed guarantee: ``require_content_access`` only catches
    ``ContentServiceUnavailableError``, so the ``AttributeError`` propagated past
    the authorization gate and the request 500ed instead of returning a 503
    denial. The docstring at content_client.py:52-56 promises the opposite --
    "callers must treat that as denial, never as allow".

    Regression test for #873.
    """
    if payload is _RAW_NULL:
        # The shared json_response() helper substitutes {} for None, which would
        # silently test the ownerless case instead. Build the null body directly.
        stub_http(
            httpx.Response(
                200,
                content=b"null",
                request=httpx.Request("GET", "http://content-service/api/v1/content/x"),
            )
        )
    else:
        stub_http(json_response(200, payload))

    with pytest.raises(ContentServiceUnavailableError):
        await resolve_content_owner(uuid4())


@pytest.mark.unit
async def test_resolve_content_owner_raises_unavailable_on_a_non_string_creator_id():
    """A numeric ``creator_id`` must fail closed too.

    ``UUID(42)`` raises ``AttributeError``, not ``ValueError``, because the
    constructor calls ``str.replace`` on its argument -- the same escape as a
    scalar body, one level down.
    """
    stub_http(json_response(200, {"creator_id": 42}))

    with pytest.raises(ContentServiceUnavailableError):
        await resolve_content_owner(uuid4())


@pytest.mark.unit
async def test_resolve_content_owner_still_returns_none_for_absent_owner():
    """The guard must not turn a legitimately ownerless item into a 503.

    Guards against over-correction: a well-formed object with a null or empty
    ``creator_id`` means "no owner", not "malformed".
    """
    for payload in ({}, {"creator_id": None}, {"creator_id": ""}):
        stub_http(json_response(200, payload))
        assert await resolve_content_owner(uuid4()) is None


# ===================== app/schemas/__init__.py nesting ======================


@pytest.mark.unit
def test_nesting_depth_ignores_an_empty_dict():
    """``_nesting_depth({}) == 0`` -- the ``default=depth`` arm for dicts."""
    assert (
        LogEventRequest.model_validate(
            {"user_id": str(uuid4()), "event_type": "x", "event_data": {}}
        ).event_data
        == {}
    )


@pytest.mark.unit
def test_nesting_depth_ignores_an_empty_list():
    """``_nesting_depth([]) == 0`` -- the ``default=depth`` arm for lists."""
    assert LogEventRequest.model_validate(
        {"user_id": str(uuid4()), "event_type": "x", "event_data": {"k": []}}
    ).event_data == {"k": []}


@pytest.mark.unit
def test_nesting_depth_is_measured_for_nested_values():
    """A structure at the limit is accepted; one level deeper is rejected."""

    def nest(depth: int) -> dict:
        payload = {"leaf": 1}
        for _ in range(depth):
            payload = {"child": payload}
        return payload

    # _nesting_depth counts the leaf as one level deeper than the wrapper
    # count, so a scalar sits at ``wrappers + 1``.
    at_limit = nest(MAX_EVENT_DATA_DEPTH - 1)
    LogEventRequest.model_validate(
        {"user_id": str(uuid4()), "event_type": "x", "event_data": at_limit}
    )

    with pytest.raises(ValueError, match="nesting depth exceeds"):
        LogEventRequest.model_validate(
            {
                "user_id": str(uuid4()),
                "event_type": "x",
                "event_data": nest(MAX_EVENT_DATA_DEPTH),
            }
        )


@pytest.mark.unit
def test_nesting_depth_accepts_a_flat_payload():
    """A flat dict/list payload is always fine."""
    LogEventRequest.model_validate(
        {
            "user_id": str(uuid4()),
            "event_type": "x",
            "event_data": {"a": 1, "b": [1, 2, 3], "c": {"d": "e"}},
        }
    )


@pytest.mark.unit
async def test_scalar_body_no_longer_escapes_the_authorization_gate():
    """The gate must convert the malformed upstream body into a 503 denial.

    End-to-end through ``require_content_access``, which only catches
    ``ContentServiceUnavailableError``. Before the #873 fix this reached the
    caller as ``AttributeError`` -- a 500 -- because the escape passed straight
    through the authorization gate instead of denying.
    """
    from fastapi import HTTPException, Request

    import app.api.analytics_routes as analytics_routes

    for payload in ("just a string", ["a", "b"], 42):
        stub_http(json_response(200, payload))

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/analytics/content/x",
                "headers": [],
                "path_params": {"content_id": str(uuid4())},
                "query_string": b"",
            }
        )

        with pytest.raises(HTTPException) as caught:
            await analytics_routes.require_content_access(
                {"user_id": uuid4(), "role": "user"}, request
            )

        assert caught.value.status_code == 503
