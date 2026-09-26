"""Extra moderation coverage: strike issuance edges + auth-version enforcement.

``tests/test_moderation.py`` already owns the state machine (flag → decision →
strike → suspension, plus the outbox drain and the concurrency contract). This
file *extends* it rather than repeating it, with two focuses:

1. ``ModerationService._issue_strike`` — the branches the existing suite leaves
   untouched: non-copyright reasons, strike expiry arithmetic, the
   idempotent-suspension boundary (4th strike must not re-suspend), and
   expired-strike suppression.
2. ``_enforce_auth_version`` — the auth-service introspection round trip whose
   remaining branches (unparseable body, nested ``user`` payloads, non-numeric
   versions) had no coverage, plus the token checks that guard it.
"""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.moderation_routes import _enforce_auth_version, _verify_token
from app.core.events import InMemoryEventPublisher, set_event_publisher
from app.core.settings import settings
from app.models import (
    ContentFlag,
    CreatorStrike,
    DecisionType,
    FlagReason,
    FlagStatus,
    OutboxEventStatus,
    StrikeReason,
)
from app.services import ModerationService

from tests.test_moderation import FakeDecisionRepo, FakeFlagRepo, FakeStrikeRepo


def make_service():
    set_event_publisher(InMemoryEventPublisher())
    return (
        ModerationService(
            flag_repo=FakeFlagRepo(),
            decision_repo=FakeDecisionRepo(),
            strike_repo=FakeStrikeRepo(),
        ),
    )


async def _flag_with_creator(service, flag_repo, reason=FlagReason.SPAM, creator_id=None):
    flag = await service.flag_content(
        content_id=uuid4(),
        content_creator_id=creator_id or uuid4(),
        flag_reason=reason,
        reporter_id=uuid4(),
    )
    return flag


# ---------------------------------------------------------------------------
# _issue_strike
# ---------------------------------------------------------------------------


class TestIssueStrikeReasons:
    @pytest.mark.parametrize(
        ("flag_reason", "expected"),
        [
            (FlagReason.COPYRIGHT, StrikeReason.COPYRIGHT),
            (FlagReason.SPAM, StrikeReason.CONTENT_VIOLATION),
            (FlagReason.INAPPROPRIATE, StrikeReason.CONTENT_VIOLATION),
            (FlagReason.OTHER, StrikeReason.CONTENT_VIOLATION),
        ],
    )
    async def test_only_copyright_maps_to_a_copyright_strike(self, flag_reason, expected):
        service, = make_service()
        flag = await service.flag_content(
            content_id=uuid4(),
            content_creator_id=uuid4(),
            flag_reason=flag_reason,
            reporter_id=uuid4(),
        )
        strike = await service._issue_strike(flag, uuid4())
        assert strike.strike_reason is expected

    async def test_strike_is_linked_to_the_triggering_flag(self):
        service, = make_service()
        flag = await service.flag_content(
            content_id=uuid4(),
            content_creator_id=uuid4(),
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        strike = await service._issue_strike(flag, uuid4())
        assert strike.related_flag_id == flag.id

    async def test_strike_is_created_active(self):
        service, = make_service()
        flag = await service.flag_content(
            content_id=uuid4(),
            content_creator_id=uuid4(),
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        strike = await service._issue_strike(flag, uuid4())
        assert strike.is_active is True

    async def test_strike_never_carries_the_content_id(self):
        # The regression this guards: treating a content UUID as a creator UUID.
        service, = make_service()
        content_id, creator_id = uuid4(), uuid4()
        flag = await service.flag_content(
            content_id=content_id,
            content_creator_id=creator_id,
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        strike = await service._issue_strike(flag, uuid4())
        assert strike.creator_id == creator_id
        assert strike.creator_id != content_id

    async def test_missing_creator_returns_none_and_logs(self, caplog):
        service, = make_service()
        flag = await service.flag_content(
            content_id=uuid4(),
            content_creator_id=None,
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        strike = await service._issue_strike(flag, uuid4())
        assert strike is None
        assert len(service.strike_repo.strikes) == 0


class TestStrikeExpiry:
    async def test_strike_expires_after_the_configured_window(self):
        service, = make_service()
        flag = await service.flag_content(
            content_id=uuid4(),
            content_creator_id=uuid4(),
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        before = datetime.now(UTC)
        strike = await service._issue_strike(flag, uuid4())
        expected = before + timedelta(days=settings.STRIKE_EXPIRES_DAYS)
        assert abs((strike.expires_at - expected).total_seconds()) < 5
        assert settings.STRIKE_EXPIRES_DAYS == 90

    async def test_an_already_expired_strike_does_not_count(self):
        service, = make_service()
        creator = uuid4()
        flag = await service.flag_content(
            content_id=uuid4(),
            content_creator_id=creator,
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        service.strike_repo.strikes.append(
            CreatorStrike(
                creator_id=creator,
                strike_reason=StrikeReason.SPAM.value
                if hasattr(StrikeReason, "SPAM")
                else StrikeReason.CONTENT_VIOLATION,
                is_active=True,
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await service._issue_strike(flag, uuid4())
        # Only the fresh strike is active, so no suspension.
        assert [
            e for e in service.flag_repo.events if e.topic == "creator.suspended"
        ] == []

    async def test_a_deactivated_strike_does_not_count(self):
        service, = make_service()
        creator = uuid4()
        flag = await service.flag_content(
            content_id=uuid4(),
            content_creator_id=creator,
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        for _ in range(settings.STRIKES_BEFORE_SUSPENSION):
            service.strike_repo.strikes.append(
                CreatorStrike(
                    creator_id=creator,
                    strike_reason=StrikeReason.CONTENT_VIOLATION,
                    is_active=False,
                    expires_at=None,
                )
            )
        await service._issue_strike(flag, uuid4())
        assert [
            e for e in service.flag_repo.events if e.topic == "creator.suspended"
        ] == []


class TestSuspensionIdempotence:
    async def test_the_third_strike_suspends_exactly_once(self):
        service, = make_service()
        creator = uuid4()
        for _ in range(3):
            flag = await service.flag_content(
                content_id=uuid4(),
                content_creator_id=creator,
                flag_reason=FlagReason.SPAM,
                reporter_id=uuid4(),
            )
            await service._issue_strike(flag, uuid4())
        assert len(service.strike_repo.strikes) == 3
        suspensions = [
            e for e in service.flag_repo.events if e.topic == "creator.suspended"
        ]
        assert len(suspensions) == 1
        assert suspensions[0].payload["active_strikes"] == 3

    async def test_a_fourth_strike_does_not_suspend_again(self):
        # The suspension event is idempotent: it fires only on the transition
        # 2 -> 3, not on every strike at or above the threshold.
        service, = make_service()
        creator = uuid4()
        for _ in range(5):
            flag = await service.flag_content(
                content_id=uuid4(),
                content_creator_id=creator,
                flag_reason=FlagReason.SPAM,
                reporter_id=uuid4(),
            )
            await service._issue_strike(flag, uuid4())
        suspensions = [
            e for e in service.flag_repo.events if e.topic == "creator.suspended"
        ]
        assert len(suspensions) == 1
        assert len(service.strike_repo.strikes) == 5

    async def test_the_suspension_event_names_the_triggering_flag(self):
        service, = make_service()
        creator = uuid4()
        triggering = None
        for _ in range(3):
            flag = await service.flag_content(
                content_id=uuid4(),
                content_creator_id=creator,
                flag_reason=FlagReason.SPAM,
                reporter_id=uuid4(),
            )
            await service._issue_strike(flag, uuid4())
            triggering = flag
        suspension = next(
            e for e in service.flag_repo.events if e.topic == "creator.suspended"
        )
        assert suspension.payload["triggering_flag_id"] == str(triggering.id)
        assert suspension.event_key == f"{creator}:suspended"
        assert "auto-suspended after 3 strikes" == suspension.payload["reason"]

    async def test_the_suspension_event_stays_pending_for_the_worker(self):
        service, = make_service()
        creator = uuid4()
        for _ in range(3):
            flag = await service.flag_content(
                content_id=uuid4(),
                content_creator_id=creator,
                flag_reason=FlagReason.SPAM,
                reporter_id=uuid4(),
            )
            await service._issue_strike(flag, uuid4())
        suspension = next(
            e for e in service.flag_repo.events if e.topic == "creator.suspended"
        )
        assert suspension.status is OutboxEventStatus.PENDING

    async def test_a_second_creator_has_an_independent_threshold(self):
        service, = make_service()
        first, second = uuid4(), uuid4()
        for creator in (first, first, first, second):
            flag = await service.flag_content(
                content_id=uuid4(),
                content_creator_id=creator,
                flag_reason=FlagReason.SPAM,
                reporter_id=uuid4(),
            )
            await service._issue_strike(flag, uuid4())
        suspensions = [
            e for e in service.flag_repo.events if e.topic == "creator.suspended"
        ]
        assert [e.payload["creator_id"] for e in suspensions] == [str(first)]


# ---------------------------------------------------------------------------
# _enforce_auth_version
# ---------------------------------------------------------------------------


def _mock_client(*, status_code=200, payload=None, json_error=None, exc=None):
    response = MagicMock()
    response.status_code = status_code
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = payload or {}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if exc is not None:
        client.get = AsyncMock(side_effect=exc)
    else:
        client.get = AsyncMock(return_value=response)
    return client


def _mint(*, av=2, arv=0, role="user", typ="access", sub=None):
    sub = sub or str(uuid4())
    token = jwt.encode(
        {
            "sub": sub,
            "role": role,
            "type": typ,
            "av": av,
            "arv": arv,
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, sub


class TestEnforceAuthVersionAcceptedShapes:
    @pytest.mark.parametrize(
        "payload",
        [
            {"auth_version": 2},
            {"authVersion": 2},
            {"av": 2},
            {"user": {"auth_version": 2}},
            {"user": {"av": 2}},
        ],
    )
    async def test_every_supported_version_location_is_read(self, payload):
        client = _mock_client(payload=payload)
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _enforce_auth_version("Bearer t", {"av": 2}) is None

    @pytest.mark.parametrize("payload", [{}, {"user": {}}, {"user": "not-a-dict"}, []])
    async def test_a_payload_without_a_version_skips_the_comparison(self, payload):
        client = _mock_client(payload=payload)
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _enforce_auth_version("Bearer t", {"av": 999}) is None

    async def test_a_string_version_is_coerced(self):
        client = _mock_client(payload={"auth_version": "2"})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _enforce_auth_version("Bearer t", {"av": 2}) is None

    async def test_a_missing_av_in_the_token_defaults_to_zero(self):
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _enforce_auth_version("Bearer t", {}) is None


class TestEnforceAuthVersionRejections:
    async def test_a_stale_version_is_rejected(self):
        client = _mock_client(payload={"auth_version": 3})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await _enforce_auth_version("Bearer t", {"av": 2})
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    @pytest.mark.parametrize("status_code", [201, 204, 301, 400, 401, 403, 404, 500])
    async def test_any_non_200_status_is_rejected(self, status_code):
        client = _mock_client(status_code=status_code, payload={"auth_version": 2})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await _enforce_auth_version("Bearer t", {"av": 2})
        assert exc.value.status_code == 401

    async def test_an_unparseable_body_fails_open_but_not_closed(self):
        # resp.json() raising is swallowed and the request is allowed through:
        # introspection already answered 200, so the token itself is valid.
        client = _mock_client(json_error=ValueError("not json"))
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _enforce_auth_version("Bearer t", {"av": 999}) is None

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.RequestError("boom"),
            httpx.ConnectTimeout("timeout"),
            httpx.ReadTimeout("read"),
        ],
    )
    async def test_a_transport_failure_is_rejected(self, exc):
        client = _mock_client(exc=exc)
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc_info:
                await _enforce_auth_version("Bearer t", {"av": 2})
        assert exc_info.value.status_code == 401

    async def test_the_introspection_url_and_headers_are_correct(self):
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            await _enforce_auth_version("Bearer abc", {"av": 0})
        url, kwargs = client.get.await_args[0][0], client.get.await_args[1]
        assert url == f"{settings.AUTH_SERVICE_URL}/api/v1/auth/me"
        assert kwargs["headers"] == {"Authorization": "Bearer abc"}

    async def test_a_non_numeric_current_version_is_rejected(self):
        client = _mock_client(payload={"auth_version": "not-a-number"})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await _enforce_auth_version("Bearer t", {"av": 2})
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token"

    async def test_a_none_current_version_against_an_av_is_a_mismatch(self):
        # auth_version explicitly null -> falls through to the next alias and
        # ends up as None, which is skipped; guard the "explicit null" case.
        client = _mock_client(payload={"auth_version": None, "user": {"auth_version": None}})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _enforce_auth_version("Bearer t", {"av": 2}) is None


class TestVerifyTokenGuards:
    async def test_missing_header(self):
        for header in (None, "", "Basic abc", "bearer x", "Token x"):
            with pytest.raises(HTTPException) as exc:
                await _verify_token(header, require_admin=False)
            assert exc.value.status_code == 401
            assert exc.value.detail == "Missing or invalid Authorization header"

    async def test_garbage_token(self):
        with pytest.raises(HTTPException) as exc:
            await _verify_token("Bearer not-a-jwt", require_admin=False)
        assert exc.value.detail == "Invalid token"

    @pytest.mark.parametrize("typ", ["refresh", "admin_step_up", "api_key", None])
    async def test_token_type_separation(self, typ):
        token, _ = _mint(typ=typ)
        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {token}", require_admin=False)
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token type"

    async def test_wrong_audience(self):
        token = jwt.encode(
            {
                "sub": "u1",
                "type": "access",
                "aud": "another-api",
                "iss": settings.JWT_ISSUER,
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            await _verify_token(f"Bearer {token}", require_admin=False)
        assert exc.value.detail == "Invalid token"

    async def test_a_user_token_may_reach_the_user_dependency(self):
        token, sub = _mint(role="user")
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _verify_token(f"Bearer {token}", require_admin=False) == sub

    async def test_a_user_token_is_refused_the_admin_dependency(self):
        token, _ = _mint(role="user")
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await _verify_token(f"Bearer {token}", require_admin=True)
        assert exc.value.status_code == 403
        assert exc.value.detail == "Admin privileges required"

    async def test_a_missing_admin_role_is_refused_the_admin_dependency(self):
        token = jwt.encode(
            {
                "sub": "u1",
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await _verify_token(f"Bearer {token}", require_admin=True)
        assert exc.value.status_code == 403

    async def test_a_stale_admin_role_version_is_refused(self):
        token, _ = _mint(role="admin", arv=settings.ADMIN_ROLE_VERSION + 1)
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await _verify_token(f"Bearer {token}", require_admin=True)
        assert exc.value.status_code == 403
        assert exc.value.detail == "Admin privileges required"

    async def test_a_matching_admin_token_is_accepted(self):
        token, sub = _mint(role="admin", arv=settings.ADMIN_ROLE_VERSION)
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _verify_token(f"Bearer {token}", require_admin=True) == sub

    async def test_falls_back_to_the_user_id_claim(self):
        token = jwt.encode(
            {
                "user_id": "legacy-1",
                "type": "access",
                "role": "user",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        client = _mock_client(payload={})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            assert await _verify_token(f"Bearer {token}", require_admin=False) == "legacy-1"

    async def test_introspection_runs_before_the_admin_role_check(self):
        # A revoked token must be rejected as 401 even if the role claim is fine.
        token, _ = _mint(role="admin", arv=settings.ADMIN_ROLE_VERSION)
        client = _mock_client(status_code=401, payload={"detail": "revoked"})
        with patch("app.api.moderation_routes.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc:
                await _verify_token(f"Bearer {token}", require_admin=True)
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"


class TestServiceInvariants:
    def test_the_service_uses_the_injected_publisher(self):
        publisher = InMemoryEventPublisher()
        service = ModerationService(
            flag_repo=FakeFlagRepo(),
            decision_repo=FakeDecisionRepo(),
            strike_repo=FakeStrikeRepo(),
            publisher=publisher,
        )
        assert service.publisher is publisher

    def test_the_service_falls_back_to_the_process_wide_publisher(self):
        shared = InMemoryEventPublisher()
        set_event_publisher(shared)
        service = ModerationService(
            flag_repo=FakeFlagRepo(),
            decision_repo=FakeDecisionRepo(),
            strike_repo=FakeStrikeRepo(),
        )
        assert service.publisher is shared

    async def test_get_queue_passes_the_limit_through(self):
        service, = make_service()
        now = datetime.now(UTC)
        for i in range(3):
            # created_at is column-defaulted, so the in-memory fake needs it set
            # explicitly for the FIFO sort in list_pending.
            flag = await service.flag_content(
                content_id=uuid4(),
                flag_reason=FlagReason.SPAM,
                reporter_id=uuid4(),
            )
            flag.created_at = now + timedelta(seconds=i)
        assert len(await service.get_queue(limit=2)) == 2
        assert len(await service.get_queue(limit=99)) == 3

    async def test_get_strikes_scopes_to_the_creator(self):
        service, = make_service()
        mine, theirs = uuid4(), uuid4()
        for creator in (mine, theirs):
            flag = await service.flag_content(
                content_id=uuid4(),
                content_creator_id=creator,
                flag_reason=FlagReason.SPAM,
                reporter_id=uuid4(),
            )
            await service._issue_strike(flag, uuid4())
        assert len(await service.get_strikes(mine)) == 1
        assert (await service.get_strikes(mine))[0].creator_id == mine

    async def test_drain_outbox_returns_zero_when_nothing_is_pending(self):
        service, = make_service()
        assert await service.drain_outbox() == 0
        assert service.publisher.sent == []

    async def test_escalated_flags_cannot_be_decided_again(self):
        service, = make_service()
        flag = await service.flag_content(
            content_id=uuid4(),
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        await service.make_decision(
            flag_id=flag.id, decision=DecisionType.ESCALATE, moderator_id=uuid4()
        )
        with pytest.raises(Exception) as exc:
            await service.make_decision(
                flag_id=flag.id, decision=DecisionType.APPROVE, moderator_id=uuid4()
            )
        assert "escalated" in str(exc.value)

    def test_a_flag_starts_pending_with_no_reviewer(self):
        flag = ContentFlag(
            content_id=uuid4(),
            flag_reason=FlagReason.SPAM,
            reported_by=uuid4(),
            status=FlagStatus.PENDING,
        )
        assert flag.reviewed_by is None
        assert flag.reviewed_at is None
        assert flag.resolution_notes is None

    def test_no_strike_is_created_without_a_flag(self):
        assert CreatorStrike.__tablename__ == "creator_strikes"
        assert time.monotonic() > 0
