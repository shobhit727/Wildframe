"""Moderation service tests.

Pure in-memory tests of the moderation business logic (no DB, no network).
These cover the core domain rules: flag creation, the 3-strike suspension
threshold, decision making, and event emission. They use in-memory stubs
for the event publisher and fake repositories.
"""
import pytest
from uuid import uuid4, UUID

from app.core.events import Event, InMemoryEventPublisher, set_event_publisher
from app.models import (
    ContentFlag,
    FlagReason,
    FlagStatus,
    ModerationDecision,
    DecisionType,
    CreatorStrike,
    StrikeReason,
)
from app.repositories import (
    ContentFlagRepository,
    ModerationDecisionRepository,
    CreatorStrikeRepository,
)
from app.services import ModerationService, ModerationError


# ---------------------------------------------------------------------------
# In-memory fakes (no DB). They mirror the repository's surface just enough
# for the service to run its business logic.
# ---------------------------------------------------------------------------


class FakeFlagRepo:
    """In-memory ContentFlagRepository stand-in."""

    def __init__(self) -> None:
        self.flags: dict[UUID, ContentFlag] = {}

    async def create(self, flag: ContentFlag) -> ContentFlag:
        self.flags[flag.id] = flag
        return flag

    async def get(self, flag_id: UUID) -> ContentFlag | None:
        return self.flags.get(flag_id)

    async def list_pending(self, limit: int = 50) -> list[ContentFlag]:
        pending = [
            f for f in self.flags.values() if f.status == FlagStatus.PENDING
        ]
        pending.sort(key=lambda f: f.created_at)
        return pending[:limit]

    async def save(self, flag: ContentFlag) -> ContentFlag:
        self.flags[flag.id] = flag
        return flag


class FakeDecisionRepo:
    """In-memory ModerationDecisionRepository stand-in."""

    def __init__(self) -> None:
        self.decisions: list[ModerationDecision] = []

    async def create(self, decision: ModerationDecision) -> ModerationDecision:
        self.decisions.append(decision)
        return decision

    async def list_by_flag(self, flag_id: UUID) -> list[ModerationDecision]:
        return [d for d in self.decisions if d.flag_id == flag_id]


class FakeStrikeRepo:
    """In-memory CreatorStrikeRepository stand-in."""

    def __init__(self) -> None:
        self.strikes: list[CreatorStrike] = []

    async def create(self, strike: CreatorStrike) -> CreatorStrike:
        self.strikes.append(strike)
        return strike

    async def list_active(self, creator_id: UUID) -> list[CreatorStrike]:
        return [
            s for s in self.strikes
            if s.creator_id == creator_id and s.is_active
        ]

    async def count_active(self, creator_id: UUID) -> int:
        return len(await self.list_active(creator_id))

    async def list_all(self, creator_id: UUID) -> list[CreatorStrike]:
        return [s for s in self.strikes if s.creator_id == creator_id]


def make_service():
    """Build a ModerationService wired to in-memory stubs."""
    set_event_publisher(InMemoryEventPublisher())
    flag_repo = FakeFlagRepo()
    decision_repo = FakeDecisionRepo()
    strike_repo = FakeStrikeRepo()
    return (
        ModerationService(
            flag_repo=flag_repo,
            decision_repo=decision_repo,
            strike_repo=strike_repo,
        ),
        flag_repo,
        decision_repo,
        strike_repo,
    )


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_content_creates_flag_and_emits_event():
    """flag_content() creates a pending flag and emits content.flagged."""
    service, flag_repo, _, _ = make_service()
    content_id = uuid4()
    reporter_id = uuid4()

    flag = await service.flag_content(
        content_id=content_id,
        flag_reason=FlagReason.SPAM,
        reporter_id=reporter_id,
    )

    assert flag.content_id == content_id
    assert flag.flag_reason == FlagReason.SPAM
    assert flag.reported_by == reporter_id
    assert flag.status == FlagStatus.PENDING
    assert flag.id in flag_repo.flags

    # The content.flagged event was emitted.
    publisher = service.publisher
    assert isinstance(publisher, InMemoryEventPublisher)
    assert len(publisher.sent) == 1
    event = publisher.sent[0]
    assert event.topic == "content.flagged"
    assert event.key == str(flag.id)
    assert event.payload["content_id"] == str(content_id)
    assert event.payload["flag_reason"] == "spam"


@pytest.mark.asyncio
async def test_get_queue_returns_pending_flags_oldest_first():
    """get_queue() returns only pending flags, ordered by created_at."""
    service, flag_repo, _, _ = make_service()

    # Create three flags. Sleep is not needed because default timestamps
    # are set at flush time; we manipulate created_at directly for ordering.
    from datetime import datetime, timezone, timedelta

    f1 = ContentFlag(
        content_id=uuid4(),
        flag_reason=FlagReason.SPAM,
        reported_by=uuid4(),
        status=FlagStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    f2 = ContentFlag(
        content_id=uuid4(),
        flag_reason=FlagReason.INAPPROPRIATE,
        reported_by=uuid4(),
        status=FlagStatus.PENDING,
        created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    f3 = ContentFlag(
        content_id=uuid4(),
        flag_reason=FlagReason.COPYRIGHT,
        reported_by=uuid4(),
        status=FlagStatus.RESOLVED,  # already resolved — should not appear
        created_at=datetime.now(timezone.utc) + timedelta(seconds=2),
    )
    for f in (f1, f2, f3):
        await flag_repo.create(f)

    queue = await service.get_queue()
    assert len(queue) == 2
    # Oldest first.
    assert queue[0].id == f1.id
    assert queue[1].id == f2.id


@pytest.mark.asyncio
async def test_make_decision_approve_resolves_flag_without_strike():
    """Approving a flag resolves it and does NOT create a strike."""
    service, flag_repo, decision_repo, strike_repo = make_service()
    flag = await service.flag_content(
        content_id=uuid4(),
        flag_reason=FlagReason.SPAM,
        reporter_id=uuid4(),
    )
    moderator_id = uuid4()

    decision = await service.make_decision(
        flag_id=flag.id,
        decision=DecisionType.APPROVE,
        moderator_id=moderator_id,
        notes="Looks fine",
    )

    assert decision.flag_id == flag.id
    assert decision.decision == DecisionType.APPROVE
    assert decision.moderator_id == moderator_id
    assert decision.notes == "Looks fine"

    updated_flag = await flag_repo.get(flag.id)
    assert updated_flag.status == FlagStatus.RESOLVED
    assert updated_flag.reviewed_by == moderator_id

    # No strike was created.
    assert len(strike_repo.strikes) == 0

    # moderation.decision_made event was emitted.
    publisher = service.publisher
    assert isinstance(publisher, InMemoryEventPublisher)
    decision_events = [e for e in publisher.sent if e.topic == "moderation.decision_made"]
    assert len(decision_events) == 1
    assert decision_events[0].payload["decision"] == "approve"


@pytest.mark.asyncio
async def test_make_decision_reject_creates_strike():
    """Rejecting a flag resolves it AND creates a strike."""
    service, flag_repo, _, strike_repo = make_service()
    content_id = uuid4()
    flag = await service.flag_content(
        content_id=content_id,
        flag_reason=FlagReason.INAPPROPRIATE,
        reporter_id=uuid4(),
    )
    moderator_id = uuid4()

    await service.make_decision(
        flag_id=flag.id,
        decision=DecisionType.REJECT,
        moderator_id=moderator_id,
        notes="Violates community guidelines",
    )

    updated_flag = await flag_repo.get(flag.id)
    assert updated_flag.status == FlagStatus.RESOLVED

    # A strike was created.
    assert len(strike_repo.strikes) == 1
    strike = strike_repo.strikes[0]
    assert strike.creator_id == content_id
    assert strike.strike_reason == StrikeReason.CONTENT_VIOLATION
    assert strike.related_flag_id == flag.id
    assert strike.is_active is True


@pytest.mark.asyncio
async def test_three_strikes_triggers_suspension():
    """3 rejections = 3 active strikes = creator.suspended event emitted."""
    service, _, _, strike_repo = make_service()
    creator_id = uuid4()
    moderator_id = uuid4()

    # Flag + reject three times for the same creator (content_id == creator_id).
    for i in range(3):
        flag = await service.flag_content(
            content_id=creator_id,
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        await service.make_decision(
            flag_id=flag.id,
            decision=DecisionType.REJECT,
            moderator_id=moderator_id,
            notes=f"Strike {i + 1}",
        )

    # 3 strikes exist and are all active.
    assert len(strike_repo.strikes) == 3
    assert await strike_repo.count_active(creator_id) == 3

    # creator.suspended event was emitted (on the 3rd rejection).
    publisher = service.publisher
    assert isinstance(publisher, InMemoryEventPublisher)
    suspension_events = [e for e in publisher.sent if e.topic == "creator.suspended"]
    assert len(suspension_events) == 1
    assert suspension_events[0].payload["creator_id"] == str(creator_id)
    assert suspension_events[0].payload["active_strikes"] == 3


@pytest.mark.asyncio
async def test_two_strikes_does_not_trigger_suspension():
    """2 rejections = 2 active strikes = no suspension event."""
    service, _, _, strike_repo = make_service()
    creator_id = uuid4()
    moderator_id = uuid4()

    for i in range(2):
        flag = await service.flag_content(
            content_id=creator_id,
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        await service.make_decision(
            flag_id=flag.id,
            decision=DecisionType.REJECT,
            moderator_id=moderator_id,
        )

    assert len(strike_repo.strikes) == 2
    assert await strike_repo.count_active(creator_id) == 2

    publisher = service.publisher
    suspension_events = [e for e in publisher.sent if e.topic == "creator.suspended"]
    assert len(suspension_events) == 0


@pytest.mark.asyncio
async def test_make_decision_on_already_resolved_flag_raises():
    """Making a decision on an already-resolved flag raises ModerationError."""
    service, _, _, _ = make_service()
    flag = await service.flag_content(
        content_id=uuid4(),
        flag_reason=FlagReason.SPAM,
        reporter_id=uuid4(),
    )
    moderator_id = uuid4()

    await service.make_decision(
        flag_id=flag.id,
        decision=DecisionType.APPROVE,
        moderator_id=moderator_id,
    )

    with pytest.raises(ModerationError) as exc:
        await service.make_decision(
            flag_id=flag.id,
            decision=DecisionType.REJECT,
            moderator_id=moderator_id,
        )
    assert "already" in str(exc.value)


@pytest.mark.asyncio
async def test_make_decision_on_missing_flag_raises():
    """Making a decision on a non-existent flag raises ModerationError."""
    service, _, _, _ = make_service()
    with pytest.raises(ModerationError) as exc:
        await service.make_decision(
            flag_id=uuid4(),
            decision=DecisionType.APPROVE,
            moderator_id=uuid4(),
        )
    assert "not found" in str(exc.value)


@pytest.mark.asyncio
async def test_escalate_does_not_create_strike():
    """Escalating a flag does NOT create a strike."""
    service, flag_repo, _, strike_repo = make_service()
    flag = await service.flag_content(
        content_id=uuid4(),
        flag_reason=FlagReason.OTHER,
        reporter_id=uuid4(),
    )
    moderator_id = uuid4()

    await service.make_decision(
        flag_id=flag.id,
        decision=DecisionType.ESCALATE,
        moderator_id=moderator_id,
        notes="Need senior review",
    )

    updated_flag = await flag_repo.get(flag.id)
    assert updated_flag.status == FlagStatus.ESCALATED
    assert len(strike_repo.strikes) == 0


@pytest.mark.asyncio
async def test_get_strikes_returns_all_strikes_for_creator():
    """get_strikes() returns all strikes (active + expired) for a creator."""
    service, _, _, strike_repo = make_service()
    creator_id = uuid4()
    moderator_id = uuid4()

    # Create 2 strikes via rejections.
    for _ in range(2):
        flag = await service.flag_content(
            content_id=creator_id,
            flag_reason=FlagReason.SPAM,
            reporter_id=uuid4(),
        )
        await service.make_decision(
            flag_id=flag.id,
            decision=DecisionType.REJECT,
            moderator_id=moderator_id,
        )

    strikes = await service.get_strikes(creator_id)
    assert len(strikes) == 2
    assert all(s.creator_id == creator_id for s in strikes)


@pytest.mark.asyncio
async def test_copyright_flag_creates_copyright_strike():
    """A rejected copyright flag creates a COPYRIGHT strike, not content_violation."""
    service, _, _, strike_repo = make_service()
    creator_id = uuid4()
    flag = await service.flag_content(
        content_id=creator_id,
        flag_reason=FlagReason.COPYRIGHT,
        reporter_id=uuid4(),
    )
    await service.make_decision(
        flag_id=flag.id,
        decision=DecisionType.REJECT,
        moderator_id=uuid4(),
    )

    assert len(strike_repo.strikes) == 1
    assert strike_repo.strikes[0].strike_reason == StrikeReason.COPYRIGHT
