import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.routes.admin import verify_admin_reauth, _stepup_jti_seen
from app.core.settings import settings
from tests._test_jwks import JWKS, PRIVATE_PEM


def _mint_step_up(
    sub,
    role="admin",
    amr=None,
    scope="admin:destructive",
    exp_offset=300,
    jti=None,
    arv=None,
    aud=None,
    iss=None,
    typ="admin_step_up",
):
    if amr is None:
        amr = ["pwd"]
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=exp_offset)
    payload = {
        "sub": str(sub),
        "user_id": str(sub),
        "role": role,
        "type": typ,
        "amr": amr,
        "scope": scope,
        "iat": now,
        "exp": exp,
        "iss": iss or settings.JWT_ISSUER,
        "aud": aud or settings.JWT_AUDIENCE,
        "arv": arv if arv is not None else settings.ADMIN_ROLE_VERSION,
        "av": 0,
        "jti": jti or f"stepup_{sub}_{now.timestamp()}_{uuid.uuid4().hex[:4]}",
    }
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


def _mint_access(sub, exp_offset=300):
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=exp_offset)
    payload = {
        "sub": str(sub),
        "user_id": str(sub),
        "role": "admin",
        "type": "access",
        "iat": now,
        "exp": exp,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "arv": settings.ADMIN_ROLE_VERSION,
        "av": 0,
        "jti": f"access_{sub}_{now.timestamp()}",
    }
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch):
    async def get_jwks(_url):
        return JWKS

    monkeypatch.setattr("app.api.routes.admin.get_cached_jwks", get_jwks)
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")


@pytest.mark.asyncio
async def test_verify_success():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_step_up(admin_id)
    result = await verify_admin_reauth(admin_id, token)
    assert result == admin_id


@pytest.mark.asyncio
async def test_rejects_access_token_type():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_access(admin_id)
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 401
    assert "Invalid token" in exc.value.detail


@pytest.mark.asyncio
async def test_rejects_expired():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_step_up(admin_id, exp_offset=-120)
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_insufficient_amr():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_step_up(admin_id, amr=[])
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 401
    assert "Insufficient authentication" in exc.value.detail
    token2 = _mint_step_up(admin_id, amr=["mfa"])
    with pytest.raises(HTTPException) as exc2:
        await verify_admin_reauth(admin_id, token2)
    assert exc2.value.status_code == 401


@pytest.mark.asyncio
async def test_cross_admin_mismatch():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    other = str(uuid.uuid4())
    token = _mint_step_up(other)
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 403
    assert "mismatch" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_jti_replay():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    jti = f"stepup_replay_{uuid.uuid4()}"
    token = _mint_step_up(admin_id, jti=jti)
    result = await verify_admin_reauth(admin_id, token)
    assert result == admin_id
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 401
    assert "already used" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_jti_different_tokens_both_ok():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    t1 = _mint_step_up(admin_id, jti=f"jti1_{uuid.uuid4()}")
    t2 = _mint_step_up(admin_id, jti=f"jti2_{uuid.uuid4()}")
    assert await verify_admin_reauth(admin_id, t1) == admin_id
    assert await verify_admin_reauth(admin_id, t2) == admin_id


@pytest.mark.asyncio
async def test_missing_header():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_role():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_step_up(admin_id, role="user")
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_wrong_scope():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_step_up(admin_id, scope="admin:read")
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 403
    assert "scope" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_missing_jti():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "role": "admin",
        "type": "admin_step_up",
        "amr": ["pwd"],
        "scope": "admin:destructive",
        "iat": now,
        "exp": now + timedelta(seconds=300),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "arv": settings.ADMIN_ROLE_VERSION,
        "av": 0,
    }
    token = jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_arv():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_step_up(admin_id, arv=999)
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, token)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invalid_signature():
    _stepup_jti_seen.clear()
    admin_id = str(uuid.uuid4())
    token = _mint_step_up(admin_id)
    bad = token[:-5] + "XXXXX"
    with pytest.raises(HTTPException) as exc:
        await verify_admin_reauth(admin_id, bad)
    assert exc.value.status_code == 401
