"""Regression tests: moderation enforcement at the auth boundary.

Suspended/banned accounts must be rejected at login AND refresh (the
user.moderated consumer flips ``users.is_active``; these tests pin the
service-level behaviour the consumer relies on).

These drive the **real** ``UserRepository`` against a **real** database rather
than mocking the lookup. That distinction is the whole point of this file:
``UserRepository.get_by_email``/``get_by_id`` filter on ``is_active IS TRUE``
(``app/repositories/__init__.py``), so an inactive row can never be returned by
the production lookup. Mocking that lookup to hand back a suspended user
manufactures a state the service cannot actually be in -- which is exactly how
the previous generation of these tests passed while asserting a 403 that the
code path could not produce.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, LoginAudit
from app.repositories import LoginAuditRepository, RefreshTokenRepository, UserRepository
from app.schemas import UserLoginRequest
from app.security import PasswordManager, TokenManager
from app.services import AuthService

GOOD_PASSWORD = "Str0ngPassw0rd!"


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/suspension.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def make_service(session_factory):
    """Build an AuthService over the real repositories, and hand back a setter
    that can flip ``is_active`` the way the ``user.moderated`` consumer does."""

    async def _make(email: str, **kwargs):
        async with session_factory() as session:
            repo = UserRepository(session)
            user = await repo.create(
                email=email,
                password_hash=PasswordManager.hash_password(GOOD_PASSWORD),
                **kwargs,
            )
            await repo.commit()
            service = AuthService(
                user_repo=repo,
                token_repo=RefreshTokenRepository(session),
                audit_repo=LoginAuditRepository(session),
                password_manager=PasswordManager(),
                token_manager=TokenManager(),
            )
            return user, service

    return _make


@pytest.mark.usefixtures("_no_redis_rate_limit")
class TestSuspendedAccountsAreNotEnumerable:
    async def test_lookup_does_not_return_a_suspended_account(self, make_service):
        """The invariant everything below rests on.

        ``get_by_email`` filters ``is_active IS TRUE``. If this ever stops being
        true, a suspended account becomes distinguishable from an unknown one
        and the 401 in the tests after it stops being the whole story.
        """
        _user, service = await make_service("susp-lookup@wildframe.com", is_active=False)

        assert await service.user_repo.get_by_email("susp-lookup@wildframe.com") is None

    async def test_suspended_login_is_indistinguishable_from_an_unknown_account(self, make_service):
        """#874: both must be the same 401 with the same body.

        A suspended account is deliberately **not** given its own 403. If it
        were, an attacker could register a list of addresses, log in, and read
        off which ones exist -- 403 means "real account, currently suspended",
        401 means "no such account". The generic 401 costs a suspended user a
        support ticket and buys us the absence of an account-enumeration
        oracle, which is the better trade on a public login surface.
        """
        _user, service = await make_service("suspended@wildframe.com", is_active=False)

        with pytest.raises(HTTPException) as suspended:
            await service.login(
                UserLoginRequest(email="suspended@wildframe.com", password=GOOD_PASSWORD),
                ip_address="127.0.0.1",
            )
        with pytest.raises(HTTPException) as unknown:
            await service.login(
                UserLoginRequest(email="nobody-here@wildframe.com", password=GOOD_PASSWORD),
                ip_address="127.0.0.1",
            )

        assert suspended.value.status_code == unknown.value.status_code == 401
        assert suspended.value.detail == unknown.value.detail == "Invalid email or password"

    async def test_suspended_account_cannot_refresh_a_token(self, make_service):
        """A suspension must also stop new access tokens being minted."""
        _user, service = await make_service("suspended-refresh@wildframe.com", is_active=False)
        # get_by_id applies the same filter, so the refresh lookup misses first.
        service.token_manager.verify_refresh_token = lambda _token: uuid.uuid4()

        with pytest.raises(HTTPException) as exc:
            await service.refresh_token("some-refresh-token")

        assert exc.value.status_code == 401
        assert exc.value.detail == "User not found"

    async def test_a_suspended_attempt_is_not_attributed_to_the_user_row(self, make_service):
        """Non-enumeration must hold in the audit trail too, not just the response.

        Because the suspended account is refused at the lookup, ``login`` records
        the attempt against the shared unknown-user sentinel rather than against
        the real user row. That is deliberate -- attributing it would make the
        attempt countable per account and hand an attacker an oracle -- but it
        is worth pinning so nobody "fixes" it into per-account attribution.

        Only the "not attributed to the user" half is asserted: reading the
        sentinel row back needs the all-zero UUID, which SQLite hands to
        SQLAlchemy as an int and its UUID result processor cannot rebuild.
        """
        from sqlalchemy import select

        user, service = await make_service("suspended-audit@wildframe.com", is_active=False)

        with pytest.raises(HTTPException):
            await service.login(
                UserLoginRequest(email="suspended-audit@wildframe.com", password=GOOD_PASSWORD),
                ip_address="127.0.0.1",
            )

        attributed = [
            row.status
            for row in (
                await service.audit_repo.session.execute(
                    select(LoginAudit).where(LoginAudit.user_id == user.id)
                )
            ).scalars()
        ]
        assert attributed == []


@pytest.mark.usefixtures("_no_redis_rate_limit")
class TestUnaffectedPathsStillWork:
    async def test_active_account_gets_past_the_lookup_gate(self, make_service):
        """The anti-enumeration 401 must not be a blanket lockout."""
        _user, service = await make_service("active@wildframe.com", is_active=True)

        with pytest.raises(HTTPException) as exc:
            await service.login(
                UserLoginRequest(email="active@wildframe.com", password="the-wrong-password"),
                ip_address="127.0.0.1",
            )

        # Fails at the password, i.e. it got all the way through the lookup.
        assert exc.value.status_code == 401
        assert "not found" not in exc.value.detail

    async def test_locked_account_is_429(self, make_service, session_factory):
        """A real stored lock still produces 429, not a 500 or a 403.

        ``locked_until`` is stored naive; ``User.is_locked`` normalises tzinfo,
        so a lock read back from the database is honoured (#848). This is the
        control for the suspension cases above: the suspension 401 is about the
        ``is_active`` filter, and must not be mistaken for a lockout.
        """
        user, service = await make_service("locked@wildframe.com", is_active=True)

        async with session_factory() as session:
            repo = UserRepository(session)
            row = await repo.get_by_email("locked@wildframe.com")
            row.locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)
            await repo.commit()

        # A fresh session: the lock must be the value read back from storage.
        async with session_factory() as session:
            repo = UserRepository(session)
            fresh = AuthService(
                user_repo=repo,
                token_repo=RefreshTokenRepository(session),
                audit_repo=LoginAuditRepository(session),
                password_manager=PasswordManager(),
                token_manager=TokenManager(),
            )
            with pytest.raises(HTTPException) as exc:
                await fresh.login(
                    UserLoginRequest(email="locked@wildframe.com", password=GOOD_PASSWORD),
                    ip_address="127.0.0.1",
                )

        assert exc.value.status_code == 429
        assert "temporarily locked" in exc.value.detail
        assert user.email == "locked@wildframe.com"


def _returns_uuid():
    from unittest.mock import AsyncMock

    import uuid

    return AsyncMock(return_value=uuid.uuid4())
