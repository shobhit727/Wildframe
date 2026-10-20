"""What the uploads ``/health`` handler derives its status from.

This file previously held one test asserting
``response.json()["status"] in ("healthy", "degraded")``. That accepts both
outcomes, so it could not distinguish a working database from a broken one: it
passed against a handler that always answered ``"degraded``, and against one that
always answered ``"healthy``. It is AGENTS.md's "test that passes because the
request succeeded" — the status code carried no information about the check the
endpoint exists to perform.

Those two *outcomes* are already pinned properly by
``tests/test_app_lifecycle.py`` (``{"status": "healthy"}`` and
``{"status": "degraded"}``), so they are not repeated here — two identical
assertions are one assertion. What was missing anywhere in the suite is the link
between the database check and the status: nothing asserted that the handler
consults ``DatabaseManager.health_check`` at all. That is what is covered below,
and it is the assertion that gives the outcome tests their meaning.

The stub replaces the real ``DatabaseManager`` class attribute, which the handler
reads at request time, so the endpoint under test is the real one.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.database import DatabaseManager
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_health():
    """Stand in for the database check; never let a test open a real connection.

    Returned rather than autouse so each test states the outcome it needs.
    """
    original = DatabaseManager.health_check
    stub = AsyncMock(return_value=True)
    DatabaseManager.health_check = stub
    yield stub
    DatabaseManager.health_check = original


def test_the_status_is_derived_from_the_database_check(client, db_health):
    """The endpoint's only claim is about the database, so it must ask.

    A handler that stopped consulting the check would keep answering 200 and a
    body; every "status" assertion elsewhere in the suite would still pass.
    """
    db_health.reset_mock()

    response = client.get("/health")

    assert response.status_code == 200
    db_health.assert_awaited_once()


@pytest.mark.parametrize("db_ok", [True, False])
def test_the_check_is_made_once_per_request(client, db_health, db_ok):
    """One probe per request: not zero, and not retried until something sounds right."""
    db_health.return_value = db_ok
    db_health.reset_mock()

    client.get("/health")

    assert db_health.await_count == 1


def test_a_healthy_response_leaks_no_dependency_detail(client, db_health):
    """Liveness only, on the healthy path.

    ``test_app_lifecycle.py`` asserts the body has no ``checks`` breakdown, but
    only against the degraded body. A ``/health`` endpoint that only leaked when
    everything was fine would pass that test, so the content check is applied to
    both outcomes here.
    """
    db_health.return_value = True

    body = client.get("/health").json()

    assert set(body) == {"status"}
    lowered = repr(body).lower()
    for leak in ("postgres", "password", "://", "dsn", "traceback", "checks"):
        assert leak not in lowered, f"/health leaked {leak!r}"
