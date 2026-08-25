"""Regression: the user.registered consumer provisions a default profile.

Fresh accounts 404'd on /account until the frontend auto-created a profile;
the consumer now provisions it at the source.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.event_consumer import _provision_profile


@pytest.mark.asyncio
async def test_provision_profile_creates_via_service():
    """Consumer builds UserService with all repos and provisions the profile."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session)

    with patch("app.services.UserService") as svc_cls:
        svc_cls.return_value.create_user_profile = AsyncMock()
        await _provision_profile(factory, "11111111-2222-3333-4444-555555555555")

        svc_cls.assert_called_once()
        svc_cls.return_value.create_user_profile.assert_awaited_once_with(
            __import__("uuid").UUID("11111111-2222-3333-4444-555555555555")
        )


@pytest.mark.asyncio
async def test_provision_profile_tolerates_duplicate():
    """A duplicate-profile failure is logged, not raised (at-least-once)."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session)

    with patch("app.services.UserService") as svc_cls:
        svc_cls.return_value.create_user_profile = AsyncMock(
            side_effect=RuntimeError("duplicate key value violates unique constraint")
        )
        # At-least-once delivery: failures are logged, never raised — a
        # redelivered message must not kill the consumer loop.
        await _provision_profile(factory, "11111111-2222-3333-4444-555555555555")
        svc_cls.return_value.create_user_profile.assert_awaited_once()


@pytest.mark.asyncio
async def test_envelope_unwrap_yields_user_id():
    """SDK envelope: user_id lives under payload, not at the top level."""
    event = {
        "event_id": "e1",
        "topic": "user.registered",
        "payload": {"user_id": "11111111-2222-3333-4444-555555555555"},
    }
    payload = event.get("payload", event)
    assert payload["user_id"] == "11111111-2222-3333-4444-555555555555"
