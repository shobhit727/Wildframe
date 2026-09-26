"""Tests for `app.repositories.privacy` and `app.repositories.dsar`.

Both repositories sit under `app/**` but are only reachable through the
unmounted `privacy.py` / `dsar.py` routers, so they had 0% coverage. Here they
are driven directly against a real aiosqlite session, which also pins the
query semantics (ordering, `granted`/`withdrawn_at` filters, SLA arithmetic and
the comma-joined `data_categories` column).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.dsar import DSARRequest
from app.models.privacy import UserConsentRecord
from app.repositories.dsar import DSARRepository
from app.repositories.privacy import UserConsentRepository


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/privacy_repos.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


async def _insert(session: AsyncSession, **kwargs) -> UserConsentRecord:
    record = UserConsentRecord(**kwargs)
    session.add(record)
    await session.flush()
    return record


def _consent(user_id, **overrides) -> dict:
    payload = {
        "user_id": user_id,
        "consent_type": "marketing",
        "jurisdiction": "EU",
        "granted": True,
        "version": "1.0.0",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# UserConsentRepository.create
# ---------------------------------------------------------------------------


async def test_create_flushes_the_record_so_it_is_readable(session: AsyncSession):
    repo = UserConsentRepository(session)
    user_id = uuid4()

    created = await repo.create(UserConsentRecord(**_consent(user_id)))

    assert created.id is not None
    assert created.user_id == user_id
    found = (await session.execute(select(UserConsentRecord))).scalar_one()
    assert found is created


# ---------------------------------------------------------------------------
# get_by_user_type_jurisdiction
# ---------------------------------------------------------------------------


async def test_get_by_user_type_jurisdiction_matches_the_exact_triple(session: AsyncSession):
    repo = UserConsentRepository(session)
    user_id = uuid4()
    await repo.create(UserConsentRecord(**_consent(user_id)))
    await session.commit()

    hit = await repo.get_by_user_type_jurisdiction(user_id, "marketing", "EU")

    assert hit is not None
    assert hit.consent_type == "marketing"


async def test_get_by_user_type_jurisdiction_is_none_for_a_different_type(session: AsyncSession):
    repo = UserConsentRepository(session)
    user_id = uuid4()
    await repo.create(UserConsentRecord(**_consent(user_id)))
    await session.commit()

    assert await repo.get_by_user_type_jurisdiction(user_id, "analytics", "EU") is None
    assert await repo.get_by_user_type_jurisdiction(user_id, "marketing", "US") is None
    assert await repo.get_by_user_type_jurisdiction(uuid4(), "marketing", "EU") is None


# ---------------------------------------------------------------------------
# get_by_user
# ---------------------------------------------------------------------------


async def test_get_by_user_returns_only_that_users_rows(session: AsyncSession):
    repo = UserConsentRepository(session)
    user_id, other = uuid4(), uuid4()
    await repo.create(UserConsentRecord(**_consent(user_id, consent_type="marketing")))
    await repo.create(UserConsentRecord(**_consent(user_id, consent_type="analytics")))
    await repo.create(UserConsentRecord(**_consent(other)))
    await session.commit()

    rows = await repo.get_by_user(user_id)

    assert len(rows) == 2
    assert {row.user_id for row in rows} == {user_id}


async def test_get_by_user_is_empty_for_an_unknown_user(session: AsyncSession):
    repo = UserConsentRepository(session)

    assert await repo.get_by_user(uuid4()) == []


async def test_get_by_user_orders_newest_first(session: AsyncSession):
    repo = UserConsentRepository(session)
    user_id = uuid4()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for offset, consent_type in enumerate(("marketing", "analytics", "profiling")):
        await repo.create(
            UserConsentRecord(
                **_consent(user_id, consent_type=consent_type, created_at=base + timedelta(days=offset))
            )
        )
    await session.commit()

    rows = await repo.get_by_user(user_id)

    assert [row.consent_type for row in rows] == ["profiling", "analytics", "marketing"]


# ---------------------------------------------------------------------------
# get_active_by_user
# ---------------------------------------------------------------------------


async def test_get_active_by_user_excludes_refused_and_withdrawn_records(session: AsyncSession):
    repo = UserConsentRepository(session)
    user_id = uuid4()
    await repo.create(UserConsentRecord(**_consent(user_id, consent_type="marketing")))
    await repo.create(
        UserConsentRecord(**_consent(user_id, consent_type="analytics", granted=False))
    )
    await repo.create(
        UserConsentRecord(
            **_consent(
                user_id,
                consent_type="profiling",
                withdrawn_at=datetime(2026, 2, 1, tzinfo=UTC),
                withdrawal_reason="user request",
            )
        )
    )
    await repo.create(UserConsentRecord(**_consent(user_id, consent_type="cookies")))
    await session.commit()

    active = await repo.get_active_by_user(user_id)

    assert {row.consent_type for row in active} == {"marketing", "cookies"}


async def test_get_active_by_user_is_empty_when_everything_is_withdrawn(session: AsyncSession):
    repo = UserConsentRepository(session)
    user_id = uuid4()
    await repo.create(
        UserConsentRecord(**_consent(user_id, withdrawn_at=datetime(2026, 2, 1, tzinfo=UTC)))
    )
    await session.commit()

    assert await repo.get_active_by_user(user_id) == []


# ---------------------------------------------------------------------------
# DSARRepository
# ---------------------------------------------------------------------------


async def test_dsar_create_comma_joins_categories_and_sets_a_30_day_sla(session: AsyncSession):
    repo = DSARRepository(session)
    user_id = uuid4()
    before = datetime.now(UTC)

    dsar = await repo.create(user_id, "access", ["profile", "devices"], reason="please")

    assert dsar.id is not None
    assert dsar.status == "pending"
    assert dsar.data_categories == "profile,devices"
    assert dsar.reason == "please"
    delta = dsar.sla_deadline - before
    assert timedelta(days=29) < delta <= timedelta(days=30, seconds=5)


async def test_dsar_create_serialises_an_empty_category_list_as_a_json_array(
    session: AsyncSession,
):
    repo = DSARRepository(session)

    dsar = await repo.create(uuid4(), "deletion", [])

    assert dsar.data_categories == "[]"
    assert dsar.reason is None


async def test_dsar_create_is_flushable_and_readable_back(session: AsyncSession):
    repo = DSARRepository(session)
    user_id = uuid4()

    dsar = await repo.create(user_id, "portability", ["profile"])
    await session.commit()

    found = (await session.execute(select(DSARRequest))).scalar_one()
    assert found.id == dsar.id


async def test_dsar_get_by_id_returns_the_row(session: AsyncSession):
    repo = DSARRepository(session)
    dsar = await repo.create(uuid4(), "correction", ["profile"])
    await session.commit()

    found = await repo.get_by_id(dsar.id)

    assert found is not None
    assert found.request_type == "correction"


async def test_dsar_get_by_id_is_none_for_an_unknown_id(session: AsyncSession):
    repo = DSARRepository(session)

    assert await repo.get_by_id(uuid4()) is None


# ---------------------------------------------------------------------------
# Router dependency providers (their bodies were hidden by the route overrides)
# ---------------------------------------------------------------------------


async def test_get_consent_repo_builds_a_repository_over_the_given_session(
    session: AsyncSession,
):
    from app.api.routes.privacy import get_consent_repo

    repo = await get_consent_repo(db=session)

    assert isinstance(repo, UserConsentRepository)
    assert repo.db is session


async def test_get_dsar_repo_builds_a_repository_over_the_given_session(
    session: AsyncSession,
):
    from app.api.routes.dsar import get_dsar_repo

    repo = await get_dsar_repo(db=session)

    assert isinstance(repo, DSARRepository)
    assert repo.db is session
