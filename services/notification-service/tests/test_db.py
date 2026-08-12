"""DB-backed integration tests: routes + repository + channels.

Uses a temp-file SQLite so every pooled connection (TestClient portal loop
included) shares one database. Mirrors the auth-service test pattern.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.notification_routes import get_current_user_id as notif_user_di
from app.channels import DeliveryError
from app.core.database import DatabaseManager
from app.main import app
from app.models import Base


@pytest.fixture
async def test_env(tmp_path):
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/test.db",
        connect_args={"timeout": 15},
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original_engine = DatabaseManager.engine
    original_factory = DatabaseManager.session_factory
    DatabaseManager.engine = test_engine
    DatabaseManager.session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    yield app

    DatabaseManager.engine = original_engine
    DatabaseManager.session_factory = original_factory
    await test_engine.dispose()


@pytest.fixture
def auth_user_id():
    return uuid4()


@pytest.fixture
def other_user_id():
    return uuid4()


@pytest.fixture
def client(test_env, auth_user_id):
    app.dependency_overrides.clear()
    app.dependency_overrides[notif_user_di] = lambda: auth_user_id
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


def send(client, user_id, title="Hello", message="World", **extra):
    payload = {"user_id": str(user_id), "title": title, "message": message, **extra}
    return client.post("/api/v1/notifications/send", json=payload)


class TestUnreadEndpoints:
    def test_unread_roundtrip_and_count(self, client, auth_user_id):
        assert client.get(f"/api/v1/notifications/unread/{auth_user_id}").json() == {
            "notifications": [],
            "total": 0,
        }
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 0
        }

        send(client, auth_user_id, title="New episode", message="S5 is out")
        send(client, auth_user_id, title="Comment", message="someone replied")
        send(client, auth_user_id, title="Read soon", message="third")

        data = client.get(f"/api/v1/notifications/unread/{auth_user_id}").json()
        assert data["total"] == 3
        assert {"New episode", "Comment", "Read soon"} == {
            n["title"] for n in data["notifications"]
        }
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 3
        }

        # Mark one read: excluded from list and count, but not deleted.
        notif_id = data["notifications"][1]["id"]
        resp = client.post(f"/api/v1/notifications/{notif_id}/read")
        assert resp.status_code == 200
        assert resp.json() == {"status": "read"}
        # Marking again stays idempotent (no 404, count unchanged).
        assert client.post(f"/api/v1/notifications/{notif_id}/read").status_code == 200
        data = client.get(f"/api/v1/notifications/unread/{auth_user_id}").json()
        assert data["total"] == 2
        assert notif_id not in {n["id"] for n in data["notifications"]}
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 2
        }

    def test_unread_other_user_inaccessible(self, client, auth_user_id, other_user_id):
        send(client, auth_user_id)
        assert client.get(f"/api/v1/notifications/unread/{other_user_id}").status_code == 403
        assert client.get(f"/api/v1/notifications/unread-count/{other_user_id}").status_code == 403

    def test_unread_pagination(self, client, auth_user_id):
        for i in range(5):
            send(client, auth_user_id, title=f"N{i}", message=f"m{i}")
        page = client.get(
            f"/api/v1/notifications/unread/{auth_user_id}", params={"limit": 2, "offset": 0}
        ).json()
        assert page["total"] == 2
        page2 = client.get(
            f"/api/v1/notifications/unread/{auth_user_id}", params={"limit": 2, "offset": 2}
        ).json()
        assert page2["total"] == 2
        assert {n["id"] for n in page["notifications"]} != {n["id"] for n in page2["notifications"]}

    def test_empty_account_returns_empty_legitimately(self, client, auth_user_id):
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 0
        }


class TestIdempotency:
    def test_duplicate_event_creates_single_notification(self, client, auth_user_id):
        event_id = uuid4()
        first = send(client, auth_user_id, event_id=str(event_id)).json()
        second = send(client, auth_user_id, event_id=str(event_id)).json()
        assert first == {"status": "sent"}
        assert second == {"status": "sent"}
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 1
        }

    def test_mark_read_and_delete_scoped_to_owner(self, client, auth_user_id, other_user_id):
        send(client, auth_user_id, title="mine")
        notif_id = client.get(f"/api/v1/notifications/unread/{auth_user_id}").json()[
            "notifications"
        ][0]["id"]

        # Other user cannot read/delete it.
        app.dependency_overrides[notif_user_di] = lambda: other_user_id
        assert client.post(f"/api/v1/notifications/{notif_id}/read").status_code == 404
        assert client.delete(f"/api/v1/notifications/{notif_id}").status_code == 404
        app.dependency_overrides[notif_user_di] = lambda: auth_user_id

        # Owner deletes; row vanishes from every read path; double delete 404s.
        assert client.delete(f"/api/v1/notifications/{notif_id}").json() == {"status": "deleted"}
        assert client.delete(f"/api/v1/notifications/{notif_id}").status_code == 404
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 0
        }
        assert client.get(f"/api/v1/notifications/unread/{auth_user_id}").json() == {
            "notifications": [],
            "total": 0,
        }


class TestPreferenceGating:
    def test_disabled_channel_is_skipped_server_side(self, client, auth_user_id):
        resp = client.put("/api/v1/notifications/preferences", json={"email_enabled": False})
        assert resp.status_code == 200
        assert resp.json()["email_enabled"] is False
        assert client.get("/api/v1/notifications/preferences").json()["email_enabled"] is False

        # Email-only send: skipped, nothing persisted.
        result = send(client, auth_user_id, channel="email").json()
        assert result["status"] == "skipped"
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 0
        }

        # Multi-channel: email skipped, in-app still delivered.
        result = send(client, auth_user_id, channels=["email", "in-app"]).json()
        assert result["status"] == "partial"
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 1
        }

    def test_unknown_preference_field_rejected(self, client):
        assert (
            client.put("/api/v1/notifications/preferences", json={"spam": True}).status_code == 422
        )
        assert client.put("/api/v1/notifications/preferences", json={}).status_code == 422


class TestChannelIsolationAndRetry:
    @pytest.fixture
    def flaky(self):
        channel = MagicMock()
        channel.name = "flaky"
        channel.deliver = AsyncMock(side_effect=[DeliveryError("smtp down"), None])
        return channel

    @pytest.fixture(autouse=True)
    def no_backoff(self):
        with patch("app.core.settings.settings.DELIVERY_RETRY_ATTEMPTS", 1):
            yield

    def test_one_failing_channel_does_not_drop_others(self, client, auth_user_id, flaky):
        registry = {"in-app": MagicMock(name="in-app"), "flaky": flaky}
        registry["in-app"].name = "in-app"
        registry["in-app"].deliver = AsyncMock()
        with patch("app.services.CHANNELS", registry):
            result = send(client, auth_user_id, channels=["in-app", "flaky"]).json()
        assert result["status"] == "partial"
        # Both channels were attempted; the row still exists.
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 1
        }

    def test_retry_redelivers_failed_channel_without_duplicates(self, client, auth_user_id, flaky):
        registry = {"in-app": MagicMock(name="in-app"), "flaky": flaky}
        registry["in-app"].name = "in-app"
        registry["in-app"].deliver = AsyncMock()
        with patch("app.services.CHANNELS", registry):
            send(client, auth_user_id, channels=["in-app", "flaky"])
            notif_id = client.get(f"/api/v1/notifications/unread/{auth_user_id}").json()[
                "notifications"
            ][0]["id"]
            retry = client.post(f"/api/v1/notifications/{notif_id}/retry")
        assert retry.status_code == 200
        assert retry.json()["status"] == "sent"
        assert client.get(f"/api/v1/notifications/unread-count/{auth_user_id}").json() == {
            "count": 1
        }
        assert flaky.deliver.await_count == 2  # initial failure + retry

    def test_retry_other_user_404(self, client, auth_user_id, other_user_id, flaky):
        registry = {"in-app": MagicMock(name="in-app"), "flaky": flaky}
        registry["in-app"].name = "in-app"
        registry["in-app"].deliver = AsyncMock()
        with patch("app.services.CHANNELS", registry):
            send(client, auth_user_id, channels=["in-app", "flaky"])
            notif_id = client.get(f"/api/v1/notifications/unread/{auth_user_id}").json()[
                "notifications"
            ][0]["id"]
            app.dependency_overrides[notif_user_di] = lambda: other_user_id
            assert client.post(f"/api/v1/notifications/{notif_id}/retry").status_code == 404

    def test_transient_failure_retried_with_backoff(self):
        from app.channels import deliver_with_retry

        channel = MagicMock()
        channel.name = "email"
        channel.deliver = AsyncMock(side_effect=[DeliveryError("boom"), None])
        with (
            patch("app.core.settings.settings.DELIVERY_RETRY_ATTEMPTS", 3),
            patch("app.core.settings.settings.DELIVERY_RETRY_BASE_DELAY", 0.01),
        ):
            import asyncio

            async def run():
                await deliver_with_retry(channel, MagicMock())

            asyncio.run(run())
        assert channel.deliver.await_count == 2


class TestTemplateSanitization:
    def test_user_fields_escaped_before_storage(self, client, auth_user_id):
        send(
            client,
            auth_user_id,
            title='<script>alert("xss")</script>',
            message="<img src=x onerror=alert(1)> Hi",
        )
        stored = client.get(f"/api/v1/notifications/unread/{auth_user_id}").json()["notifications"][
            0
        ]
        assert "<script>" not in stored["title"]
        assert "<script>" in stored["title"]
        assert "<img" not in stored["message"]
        assert "<img" in stored["message"]

    def test_email_template_renders_escaped_html(self):
        from app.channels import EmailChannel
        from app.templates import render_template

        subject, html_body, text_body = render_template(
            "new_episode", title="<script>s</script>", message="<b>bold-ish</b>"
        )
        assert "<script>" not in html_body
        assert "<script>" in html_body
        # text_body from explicit template: tags stripped, no entities
        assert "New episode:" in text_body
        assert "<script>" not in text_body
        assert "<b>" not in text_body
        assert "bold-ish" in text_body

        channel = EmailChannel()
        smtp_mock = MagicMock()
        with (
            patch("app.channels.smtplib.SMTP", return_value=smtp_mock),
            patch("app.core.settings.settings.SMTP_HOST", "smtp.test"),
        ):
            import asyncio

            async def run():
                await channel.deliver(
                    MagicMock(title="<script>alert(1)</script>", message="hello"),
                    recipient="user@example.com",
                    template="generic",
                )

            asyncio.run(run())
        sent = smtp_mock.__enter__.return_value.send_message.call_args.args[0]
        html_part = sent.get_body(preferencelist=("html",)).get_content()
        assert "<script>" not in html_part
        assert "<script>alert(1)</script>" in html_part
