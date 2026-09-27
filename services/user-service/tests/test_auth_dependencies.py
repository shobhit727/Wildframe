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
import wildframe_auth
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
from tests._test_jwks import JWKS, PRIVATE_PEM
from wildframe_auth.verifier import clear_jwks_cache

# The autouse conftest fixture already stubs httpx.AsyncClient with a 200/{} reply.
BEARER = {"Authorization": "Bearer placeholder"}


class _Endpoint:
    """The auth service's JWKS endpoint, as the service sees it."""

    def __init__(self) -> None:
        self.jwks = JWKS
        self.raises: Exception | None = None

    async def fetch(self, url: str):
        if self.raises is not None:
            raise self.raises
        return self.jwks


@pytest.fixture(autouse=True)
def _stub_jwks_endpoint(monkeypatch):
    """Serve the test JWKS and clear the SDK cache around every test."""
    ep = _Endpoint()
    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", ep.fetch)
    clear_jwks_cache()
    yield ep
    clear_jwks_cache()


def _token(**claims) -> str:
    """Mint a real RS256 access token signed by the test JWKS key.

    Signed with RS256 over the in-memory RSA key from ``tests/_test_jwks.py``,
    because the service no longer accepts a shared-secret HS256 token at all.
    """
    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "av": 0,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        **claims,
    }
    return jwt.encode(
        {k: v for k, v in payload.items() if v is not None},
        PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": "k1"},
    )


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
    """An RS256 token from an RSA key that is not in the JWKS is refused."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    intruder = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "av": 0,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        intruder.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {forged}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid or expired token"


async def test_get_current_user_id_rejects_an_expired_token():
    # 1h, not 1m: the shared verifier allows ~60s of clock skew, so a token that
    # expired "just now" is *meant* to pass.
    expired = _token(exp=datetime.now(UTC) - timedelta(hours=1))

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {expired}")

    assert excinfo.value.status_code == 401


async def test_get_current_user_id_rejects_a_token_without_a_sub_claim():
    """``sub`` is a required claim, so this is refused during verification."""
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {_token(sub=None)}")

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
    """A ``sub`` of ``None`` never reaches the subject guard, so it is a 401.

    Under the deleted HS256 path python-jose refused a non-string ``sub`` at
    decode time, producing a 401. The shared verifier is stricter still: ``sub``
    is a *required claim*, so a token without one is rejected during
    verification and the detail is the generic "Invalid or expired token".
    """
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(authorization=f"Bearer {_token(sub=None)}")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid or expired token"


async def test_a_non_string_sub_is_refused_by_the_verifier_not_the_route():
    """A non-string ``sub`` never reaches the route's subject guard.

    Worth pinning because it closes off a latent 500: the route guards
    ``UUID(payload["sub"])`` with ``except (ValueError, TypeError)``, but a
    non-string ``sub`` makes ``UUID()`` raise ``AttributeError``, which would
    escape as a 500 rather than a 401. That gap is unreachable -- the shared
    verifier refuses a non-string ``sub`` while decoding, so the route sees
    ``None`` and answers 401. Same subject as
    ``test_known_defect_require_self_only_catches_value_and_type_errors``, which
    is reachable because ``require_self`` parses the *path* parameter instead.
    """
    for bad_sub in (12345, 3.5, [1]):
        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id(authorization=f"Bearer {_token(sub=bad_sub)}")

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
