"""Tests for the auth dependencies in `app.api.routes`.

`get_current_user_id`, `require_self` and `get_user_service` are the three
guards every user-service route passes through. The existing route tests
*override* all three, so the guard bodies themselves were unreached. Here they
are called directly, with the DENY paths asserted precisely - a false accept in
this file is a cross-tenant data leak.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import get_current_user_id, get_user_service, require_self
from app.core.settings import settings
from app.models import Base
from app.repositories import (
    UserDeviceRepository,
    UserPreferenceRepository,
    UserProfileRepository,
    UserSubscriptionProfileRepository,
)
from app.services import UserService

# The autouse conftest fixture already stubs httpx.AsyncClient with a 200/{} reply.
BEARER = {"Authorization": "Bearer placeholder"}


def _token(**claims) -> str:
    payload = {
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        **claims,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _request(path_params: dict | None):
    request = MagicMock()
    request.path_params = {} if path_params is None else path_params
    return request


# ---------------------------------------------------------------------------
# get_current_user_id - accept
# ---------------------------------------------------------------------------


async def test_get_current_user_id_returns_the_sub_as_a_uuid():
    subject = uuid4()

    user_id = await get_current_user_id(authorization=f"Bearer {_token(sub=str(subject))}")

    assert isinstance(user_id, UUID)
    assert user_id == subject


# ---------------------------------------------------------------------------
# get_current_user_id - deny
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Basic abc123",
        "bearer lowercase-is-not-accepted",
        "Token abc123",
    ],
)
async def test_get_current_user_id_rejects_a_missing_or_malformed_header(header):
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=header)

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Missing or invalid authorization header"
    assert excinfo.value.headers["WWW-Authenticate"] == "Bearer"


async def test_get_current_user_id_rejects_an_invalid_signature():
    forged = jwt.encode(
        {"sub": str(uuid4()), "type": "access", "aud": settings.JWT_AUDIENCE},
        "not-the-right-signing-key-at-all-32-chars",
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {forged}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid or expired token"


async def test_get_current_user_id_rejects_an_expired_token():
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {expired}")

    assert excinfo.value.status_code == 401


async def test_get_current_user_id_rejects_a_token_without_a_sub_claim():
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {_token()}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid or expired token"


async def test_get_current_user_id_rejects_a_refresh_token():
    refresh = _token(sub=str(uuid4()), type="refresh")

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {refresh}")

    assert excinfo.value.status_code == 401


async def test_get_current_user_id_rejects_a_non_uuid_sub():
    """A signature-valid token whose `sub` is not a UUID must not authenticate."""
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {_token(sub='admin')}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid token subject"
    assert excinfo.value.headers["WWW-Authenticate"] == "Bearer"


async def test_get_current_user_id_rejects_a_none_sub():
    """python-jose refuses a non-string `sub` at decode, so this is a 401 too."""
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {_token(sub=None)}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid or expired token"


# ---------------------------------------------------------------------------
# require_self
# ---------------------------------------------------------------------------


async def test_require_self_accepts_a_matching_path_user_id():
    user_id = uuid4()

    result = await require_self(jwt_user_id=user_id, request=_request({"user_id": str(user_id)}))

    assert result == user_id


async def test_require_self_rejects_another_users_id():
    mine, theirs = uuid4(), uuid4()

    with pytest.raises(HTTPException) as excinfo:
        await require_self(jwt_user_id=mine, request=_request({"user_id": str(theirs)}))

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Not authorized to access this user"


async def test_require_self_requires_a_user_id_path_parameter():
    with pytest.raises(HTTPException) as excinfo:
        await require_self(jwt_user_id=uuid4(), request=_request({}))

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "user_id path parameter is required"


async def test_require_self_rejects_a_non_uuid_path_parameter():
    with pytest.raises(HTTPException) as excinfo:
        await require_self(jwt_user_id=uuid4(), request=_request({"user_id": "not-a-uuid"}))

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid user_id path parameter"


async def test_known_defect_require_self_only_catches_value_and_type_errors():
    """Characterisation test for a reported gap (NOT an assertion of intent).

    `app/api/routes/__init__.py:130` guards `UUID(path_user_id_raw)` with
    ``except (ValueError, TypeError)``, but a non-string (e.g. an int handed in
    by a hand-rolled test or a future non-annotated path param) makes
    ``UUID()`` raise ``AttributeError``, which escapes the guard. Starlette
    always supplies strings here, so this is defence-in-depth only.

    Expected to change when production code is fixed.
    """
    with pytest.raises(AttributeError):
        await require_self(jwt_user_id=uuid4(), request=_request({"user_id": 12345}))


# ---------------------------------------------------------------------------
# get_user_service
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def real_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/auth_deps.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_get_user_service_wires_all_four_repositories(real_session: AsyncSession):
    service = await get_user_service(session=real_session)

    assert isinstance(service, UserService)
    assert isinstance(service.profile_repo, UserProfileRepository)
    assert isinstance(service.device_repo, UserDeviceRepository)
    assert isinstance(service.preference_repo, UserPreferenceRepository)
    assert isinstance(service.subscription_repo, UserSubscriptionProfileRepository)
    assert service.profile_repo.session is real_session


async def test_get_user_service_passes_the_same_session_to_every_repo(real_session: AsyncSession):
    service = await get_user_service(session=real_session)

    assert service.device_repo.session is real_session
    assert service.preference_repo.session is real_session
    assert service.subscription_repo.session is real_session
