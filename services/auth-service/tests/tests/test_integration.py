"""
End-to-end integration tests for the Auth Service against a real Postgres.

Uses testcontainers to spin up a throwaway PostgreSQL instance, so Docker must
be available on the host. The module self-skips when Docker is missing or
unreachable, so CI stays green on both docker and non-docker runners.

Run locally with:
    poetry run pytest tests/test_integration.py -v
"""

import pytest

pytest.importorskip("testcontainers")

from testcontainers.postgres import PostgresContainer

from app.models import Base

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg():  # noqa: ANN201
    try:
        with PostgresContainer(
            "postgres:16-alpine", username="test", password="test", dbname="test"
        ) as c:
            yield c
    except Exception:  # noqa: BLE001
        pytest.skip("Could not start Postgres testcontainer (Docker unavailable?)")


def _pg_url(pg) -> str:
    port = pg.get_exposed_port(5432)
    host = pg.get_container_host_ip()
    return f"postgresql+asyncpg://test:test@{host}:{port}/test"


@pytest.mark.asyncio
async def test_register_then_login_integration(pg):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_pg_url(pg))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    from app.repositories import (
        LoginAuditRepository,
        RefreshTokenRepository,
        UserRepository,
    )
    from app.schemas import UserLoginRequest, UserRegisterRequest
    from app.security import PasswordManager, TokenManager
    from app.services import AuthService

    async with Session() as session:
        svc = AuthService(
            user_repo=UserRepository(session),
            token_repo=RefreshTokenRepository(session),
            audit_repo=LoginAuditRepository(session),
            password_manager=PasswordManager(),
            token_manager=TokenManager(),
        )

        user = await svc.register(
            UserRegisterRequest(
                email="integ@example.com",
                password="SecurePass123!",
                first_name="Int",
                last_name="Test",
            )
        )
        await session.commit()

        tokens = await svc.login(
            UserLoginRequest(email="integ@example.com", password="SecurePass123!"),
            ip_address="127.0.0.1",
        )
        await session.commit()

        assert user.email == "integ@example.com"
        assert tokens.access_token
        assert tokens.refresh_token

    await engine.dispose()