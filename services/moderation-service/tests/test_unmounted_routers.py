"""Behavioural tests for the unmounted DMCA router.

``app/main.py`` mounts only ``app.api.moderation_routes``; ``app/api/routes/
dmca.py`` is implemented (26 statements) but never included, so nothing
exercised it.

The router is mounted here into a *throwaway* ``FastAPI()`` — production
``main.py`` is left untouched, the user explicitly chose "no refactor" — and
driven over HTTP against a real aiosqlite session so the handlers' inserts,
lookups and status transitions are genuinely executed.
"""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.dmca import router
from app.core.database import get_db
from app.models.dmca import DMCATakedown
from app.models.dmca import Base as DmcaBase


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # DMCATakedown has its own declarative base, distinct from app.models.Base.
        await conn.run_sync(DmcaBase.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db_override():
        async with factory() as session:
            yield session

    throwaway = FastAPI()
    throwaway.include_router(router)
    throwaway.dependency_overrides[get_db] = _db_override
    async with AsyncClient(
        transport=ASGITransport(app=throwaway), base_url="http://stub"
    ) as http:
        yield http, factory
    await engine.dispose()


async def _create_takedown(client, **overrides):
    payload = {
        "content_id": str(uuid4()),
        "reporter_email": "reporter@example.com",
        "reason": "infringing copy",
    }
    payload.update(overrides)
    return await client.post("/dmca/takedown", json=payload)


class TestRouterIsUnmounted:
    def test_dmca_paths_are_not_served_by_the_production_app(self):
        from app.main import app as production_app

        served = set(production_app.openapi()["paths"])
        assert not [p for p in served if p.startswith("/dmca")]
        assert served == {
            "/api/v1/moderation/decisions",
            "/api/v1/moderation/flags",
            "/api/v1/moderation/health",
            "/api/v1/moderation/queue",
            "/api/v1/moderation/strikes/{creator_id}",
            "/health",
            "/metrics",
        }

    def test_the_router_declares_its_prefix_and_verbs(self):
        paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
        assert ("/dmca/takedown", ("POST",)) in paths
        assert ("/dmca/counter", ("POST",)) in paths


class TestCreateTakedown:
    async def test_returns_201_with_a_pending_id(self, client):
        http, _ = client
        resp = await _create_takedown(http)
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["id"]

    async def test_persists_the_row(self, client):
        http, factory = client
        content_id = uuid4()
        resp = await _create_takedown(http, content_id=str(content_id), reason="piracy")
        async with factory() as session:
            row = (
                await session.execute(
                    select(DMCATakedown).where(DMCATakedown.content_id == content_id)
                )
            ).scalar_one()
        assert str(row.id) == resp.json()["id"]
        assert row.reporter_email == "reporter@example.com"
        assert row.reason == "piracy"
        assert row.status == "pending"
        assert row.counter_notice is None
        assert row.repeat_infringer_count == 0

    async def test_rejects_a_missing_reporter_email(self, client):
        http, _ = client
        resp = await http.post(
            "/dmca/takedown", json={"content_id": "c-1", "reason": "x"}
        )
        assert resp.status_code == 422

    async def test_rejects_a_malformed_body(self, client):
        http, _ = client
        resp = await http.post("/dmca/takedown", json={"nope": 1})
        assert resp.status_code == 422


class TestCounterNotice:
    async def test_marks_the_takedown_countered(self, client):
        http, factory = client
        created = await _create_takedown(http)
        takedown_id = created.json()["id"]

        resp = await http.post(
            "/dmca/counter",
            json={"takedown_id": takedown_id, "counter_reason": "fair use"},
        )
        assert resp.status_code == 201
        assert resp.json() == {"id": takedown_id, "status": "countered"}

        async with factory() as session:
            rows = (await session.execute(select(DMCATakedown))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "countered"
        assert rows[0].counter_notice == "fair use"

    async def test_unknown_takedown_reports_not_found(self, client):
        http, _ = client
        resp = await http.post(
            "/dmca/counter",
            json={
                "takedown_id": "00000000-0000-0000-0000-000000000000",
                "counter_reason": "fair use",
            },
        )
        # The handler answers 201 with an error body rather than 404.
        assert resp.status_code == 201
        assert resp.json() == {"error": "not found"}

    async def test_rejects_a_malformed_counter_body(self, client):
        http, _ = client
        resp = await http.post("/dmca/counter", json={"takedown_id": "x"})
        assert resp.status_code == 422

    async def test_counter_notice_does_not_disturb_other_rows(self, client):
        http, factory = client
        first_id = uuid4()
        second_id = uuid4()
        first = await _create_takedown(http, content_id=str(first_id))
        await _create_takedown(http, content_id=str(second_id))
        await http.post(
            "/dmca/counter",
            json={"takedown_id": first.json()["id"], "counter_reason": "fair use"},
        )
        async with factory() as session:
            rows = (await session.execute(select(DMCATakedown))).scalars().all()
        by_content = {r.content_id: r.status for r in rows}
        assert by_content == {first_id: "countered", second_id: "pending"}
