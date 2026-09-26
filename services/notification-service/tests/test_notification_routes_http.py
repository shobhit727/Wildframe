"""HTTP contract of the notification routes (body / kwarg handling).

`tests/test_routes.py` covers the send endpoint's happy path and its 403s; this
file covers the remaining request-shape branches: the optional `template`
kwarg, the empty preference update, and the send path that loses an insert race
(`create` returning `created=False`).

`tests/test_repositories.py` covers the repository happy paths; the concurrency
branches live in test_repository_edge_cases.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.notification_routes import get_current_user_id, get_notif_service
from app.core.database import DatabaseManager
from app.main import create_app


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def service():
    mock = MagicMock()
    mock.send_notification = AsyncMock(return_value={"status": "sent"})
    mock.update_preferences = AsyncMock(
        return_value={
            "in_app_enabled": True,
            "email_enabled": True,
            "push_disabled": True,
            "sms_enabled": True,
        }
    )
    return mock


@pytest.fixture
def client(user_id, service):
    app = create_app()
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_notif_service] = lambda: service
    with patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=True)):
        with patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)):
            with TestClient(app, base_url="http://localhost") as test_client:
                yield test_client


def _send(client, user_id, **overrides):
    payload = {
        "user_id": str(user_id),
        "title": "New episode",
        "message": "Season 5 is out",
    }
    payload.update(overrides)
    return client.post("/api/v1/notifications/send", json=payload)


# ---------------------------------------------------------------------------
# send - optional kwargs
# ---------------------------------------------------------------------------


def test_a_non_generic_template_is_forwarded_to_the_service(client, service, user_id):
    response = _send(client, user_id, template="welcome")

    assert response.status_code == 200
    assert service.send_notification.await_args.kwargs["template"] == "welcome"


def test_the_generic_template_is_not_forwarded(client, service, user_id):
    """The default is omitted so the service keeps its own default."""
    _send(client, user_id, template="generic")

    assert "template" not in service.send_notification.await_args.kwargs


def test_a_channels_list_is_forwarded(client, service, user_id):
    response = _send(client, user_id, channels=["in-app"])

    assert response.status_code == 200
    assert service.send_notification.await_args.kwargs["channels"] == ["in-app"]


def test_an_event_id_is_forwarded_for_idempotency(client, service, user_id):
    event_id = uuid4()

    _send(client, user_id, event_id=str(event_id))

    assert service.send_notification.await_args.kwargs["event_id"] == event_id


def test_omitted_optional_fields_are_not_forwarded(client, service, user_id):
    _send(client, user_id)

    kwargs = service.send_notification.await_args.kwargs
    assert set(kwargs) == set()


# ---------------------------------------------------------------------------
# preferences
# ---------------------------------------------------------------------------


def test_an_empty_preference_update_is_rejected(client, service):
    response = client.put("/api/v1/notifications/preferences", json={})

    assert response.status_code == 422
    assert response.json()["detail"] == "No preference fields provided"
    service.update_preferences.assert_not_awaited()


def test_a_preference_update_with_only_nulls_is_rejected(client, service):
    response = client.put(
        "/api/v1/notifications/preferences",
        json={"in_app_enabled": None, "email_enabled": None},
    )

    assert response.status_code == 422
    service.update_preferences.assert_not_awaited()


def test_a_partial_preference_update_only_sends_the_set_flags(client, service):
    response = client.put(
        "/api/v1/notifications/preferences", json={"sms_enabled": False}
    )

    assert response.status_code == 200
    service.update_preferences.assert_awaited_once()
    user_id, flags = service.update_preferences.await_args.args
    assert flags == {"sms_enabled": False}


# ---------------------------------------------------------------------------
# send - the create race
# ---------------------------------------------------------------------------


async def test_send_returns_the_existing_status_when_the_insert_loses_the_race():
    """A concurrent duplicate must not re-deliver or create a second row."""
    from app.repositories import NotificationRepository
    from app.services import NotificationService

    user_id = uuid4()
    event_id = uuid4()
    winner = MagicMock()
    winner.delivery_status = "sent"
    winner.id = uuid4()

    repo = MagicMock()
    # `send_notification` checks get_by_event_id first and finds nothing...
    repo.get_by_event_id = AsyncMock(return_value=None)
    # ...but the INSERT loses the unique-index race, so `create` reports the
    # row that another request already committed.
    repo.create = AsyncMock(return_value=(winner, False))
    repo.get_preference = AsyncMock(
        return_value=MagicMock(
            in_app_enabled=True, email_enabled=True, push_enabled=True, sms_enabled=True
        )
    )
    repo.session = MagicMock()
    repo.session.commit = AsyncMock()
    repo.parse_delivery_errors = NotificationRepository.parse_delivery_errors

    result = await NotificationService(repo).send_notification(
        user_id, "Title", "Message", event_id=event_id
    )

    assert result == {"status": "sent"}
    # No channel was dispatched and nothing was committed over the winner.
    repo.session.commit.assert_not_awaited()


async def test_send_is_idempotent_for_a_known_event_id():
    """The first check short-circuits before any insert or dispatch."""
    from app.repositories import NotificationRepository
    from app.services import NotificationService

    event_id = uuid4()
    existing = MagicMock()
    existing.delivery_status = "partial"

    repo = MagicMock()
    repo.get_by_event_id = AsyncMock(return_value=existing)
    repo.get_preference = AsyncMock(
        return_value=MagicMock(
            in_app_enabled=True, email_enabled=True, push_enabled=True, sms_enabled=True
        )
    )
    repo.session = MagicMock()
    repo.session.commit = AsyncMock()
    repo.parse_delivery_errors = NotificationRepository.parse_delivery_errors
    repo.create = AsyncMock()

    result = await NotificationService(repo).send_notification(
        uuid4(), "Title", "Message", event_id=event_id
    )

    assert result == {"status": "partial"}
    repo.create.assert_not_awaited()
    repo.session.commit.assert_not_awaited()
