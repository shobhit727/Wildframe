"""Moderation service business logic.

Owns the content review workflow: flagging content, the moderator queue,
decisions (approve / reject / escalate), creator strikes, and automatic
suspension when a creator accumulates 3 active strikes.

Infrastructure (event bus) is injected via ports so this class is pure
domain logic and unit-testable with stubs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.events import Event, EventPublisher, get_event_publisher
from app.core.settings import settings
from app.models import (
    ContentFlag,
    CreatorStrike,
    DecisionType,
    FlagReason,
    FlagStatus,
    ModerationDecision,
    StrikeReason,
)
from app.repositories import (
    ContentFlagRepository,
    CreatorStrikeRepository,
    ModerationDecisionRepository,
)

logger = logging.getLogger(__name__)


class ModerationError(Exception):
    """Domain error for the moderation workflow."""


class ModerationService:
    """Orchestrates content flagging, review, decisions, and strikes."""

    def __init__(
        self,
        flag_repo: ContentFlagRepository,
        decision_repo: ModerationDecisionRepository,
        strike_repo: CreatorStrikeRepository,
        publisher: EventPublisher | None = None,
    ) -> None:
        self.flag_repo = flag_repo
        self.decision_repo = decision_repo
        self.strike_repo = strike_repo
        self.publisher = publisher or get_event_publisher()

    # ------------------------------------------------------------------
    # flag_content
    # ------------------------------------------------------------------

    async def flag_content(
        self,
        *,
        content_id: UUID,
        flag_reason: FlagReason,
        reporter_id: UUID,
        content_creator_id: UUID | None = None,
    ) -> ContentFlag:
        """Flag a piece of content for moderator review.

        Creates a ``ContentFlag`` row in ``pending`` status and emits a
        ``content.flagged`` event so the notification service can alert
        moderators and the analytics service can track flag volume.

        ``content_creator_id`` is the authoritative UUID of the creator who
        owns ``content_id``. Callers (api-gateway / admin) resolve it from
        content-service before flagging so that downstream strike issuance
        never confuses a content UUID for a creator UUID. ``None`` means the
        creator could not be resolved; the flag is still recorded but a
        rejection cannot issue a strike until the field is backfilled.
        """
        flag = ContentFlag(
            content_id=content_id,
            content_creator_id=content_creator_id,
            flag_reason=flag_reason,
            reported_by=reporter_id,
            status=FlagStatus.PENDING,
        )
        await self.flag_repo.create(flag)

        await self.publisher.publish(
            Event(
                topic="content.flagged",
                key=str(flag.id),
                payload={
                    "flag_id": str(flag.id),
                    "content_id": str(content_id),
                    "flag_reason": flag_reason.value,
                    "reporter_id": str(reporter_id),
                },
            )
        )
        logger.info(
            "content flagged: flag_id=%s content_id=%s reason=%s reporter=%s",
            flag.id,
            content_id,
            flag_reason.value,
            reporter_id,
        )
        return flag

    # ------------------------------------------------------------------
    # get_queue
    # ------------------------------------------------------------------

    async def get_queue(self, limit: int = 50) -> list[ContentFlag]:
        """Return the pending review queue, oldest first."""
        return await self.flag_repo.list_pending(limit=limit)

    # ------------------------------------------------------------------
    # make_decision
    # ------------------------------------------------------------------

    async def make_decision(
        self,
        *,
        flag_id: UUID,
        decision: DecisionType,
        moderator_id: UUID,
        notes: str | None = None,
    ) -> ModerationDecision:
        """Record a moderator's decision on a flag.

        Side effects:
            * Persists a ``ModerationDecision`` row (audit trail).
            * Updates the flag's status (resolved or escalated).
            * On **reject**: creates a ``CreatorStrike`` against the
              content's creator. If this is the 3rd active strike, also
              emits a ``creator.suspended`` event.
            * Emits a ``moderation.decision_made`` event.

        Raises ``ModerationError`` if the flag doesn't exist or has already
        been resolved/escalated.
        """
        flag = await self.flag_repo.get(flag_id)
        if flag is None:
            raise ModerationError(f"flag {flag_id} not found")
        if flag.status in (FlagStatus.RESOLVED, FlagStatus.ESCALATED):
            raise ModerationError(
                f"flag {flag_id} already {flag.status.value}; no further decisions"
            )

        # Record the decision (audit trail).
        mod_decision = ModerationDecision(
            flag_id=flag_id,
            moderator_id=moderator_id,
            decision=decision,
            notes=notes,
        )
        await self.decision_repo.create(mod_decision)

        # Update the flag's status.
        flag.reviewed_by = moderator_id
        flag.reviewed_at = datetime.now(UTC)
        flag.resolution_notes = notes
        if decision == DecisionType.ESCALATE:
            flag.status = FlagStatus.ESCALATED
        else:
            flag.status = FlagStatus.RESOLVED
        await self.flag_repo.save(flag)

        # On reject: issue a strike against the flag's authoritative creator.
        # ``_issue_strike`` skips strike creation (and logs) when
        # ``flag.content_creator_id`` is missing — we never infer creator
        # identity from ``content_id``.
        if decision == DecisionType.REJECT:
            await self._issue_strike(flag, moderator_id)

        # Emit decision event.
        await self.publisher.publish(
            Event(
                topic="moderation.decision_made",
                key=str(flag.id),
                payload={
                    "flag_id": str(flag.id),
                    "content_id": str(flag.content_id),
                    "decision": decision.value,
                    "moderator_id": str(moderator_id),
                    "notes": notes,
                },
            )
        )
        logger.info(
            "moderation decision: flag_id=%s decision=%s moderator=%s",
            flag_id,
            decision.value,
            moderator_id,
        )
        return mod_decision

    # ------------------------------------------------------------------
    # _issue_strike (private)
    # ------------------------------------------------------------------

    async def _issue_strike(self, flag: ContentFlag, moderator_id: UUID) -> CreatorStrike | None:
        """Issue a strike for a rejected flag and check suspension threshold.

        The strike is linked to the flag that caused it (``related_flag_id``)
        and expires after ``STRIKE_EXPIRES_DAYS`` days. If the creator now has
        >= ``STRIKES_BEFORE_SUSPENSION`` active strikes, emit
        ``creator.suspended``.

        The strike is issued against ``flag.content_creator_id`` — the
        authoritative creator UUID owning ``flag.content_id``. If the creator
        was not resolved when the flag was raised (``content_creator_id`` is
        ``None``), no strike is issued: the creator UUID can never be inferred
        from ``content_id`` and the strike is skipped with an audit log so an
        operator can backfill the flag and re-run the decision.
        """
        if flag.content_creator_id is None:
            logger.warning(
                "strike skipped: flag_id=%s content_id=%s has no authoritative "
                "content_creator_id; backfill before re-deciding",
                flag.id,
                flag.content_id,
            )
            return None
        creator_id = flag.content_creator_id
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.STRIKE_EXPIRES_DAYS)

        # Derive the strike reason from the flag reason.
        # A copyright flag -> copyright strike; everything else -> content_violation.
        if flag.flag_reason == FlagReason.COPYRIGHT:
            strike_reason = StrikeReason.COPYRIGHT
        else:
            strike_reason = StrikeReason.CONTENT_VIOLATION

        strike = CreatorStrike(
            creator_id=creator_id,
            strike_reason=strike_reason,
            related_flag_id=flag.id,
            is_active=True,
            expires_at=expires_at,
        )
        await self.strike_repo.create(strike)

        # Check suspension threshold.
        active_count = await self.strike_repo.count_active(creator_id)
        if active_count >= settings.STRIKES_BEFORE_SUSPENSION:
            await self.publisher.publish(
                Event(
                    topic="creator.suspended",
                    key=str(creator_id),
                    payload={
                        "creator_id": str(creator_id),
                        "active_strikes": active_count,
                        "reason": "auto-suspended after " f"{active_count} strikes",
                        "triggering_flag_id": str(flag.id),
                    },
                )
            )
            logger.warning(
                "creator auto-suspended: creator_id=%s active_strikes=%d",
                creator_id,
                active_count,
            )
        return strike

    # ------------------------------------------------------------------
    # get_strikes
    # ------------------------------------------------------------------

    async def get_strikes(self, creator_id: UUID) -> list[CreatorStrike]:
        """List all strikes (active + expired) for a creator."""
        return await self.strike_repo.list_all(creator_id)
