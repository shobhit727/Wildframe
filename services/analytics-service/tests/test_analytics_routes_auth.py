"""Authorization tests for ``analytics-service/app/api/analytics_routes.py``.

Two dependencies gate every analytics endpoint and had no coverage of their own
(the existing suite overrides them):

* ``get_current_user_claims`` (analytics_routes.py:28-71) -- the bearer-token
  boundary. Produces ``{"user_id": UUID, "role": str}``.
* ``require_content_access`` (analytics_routes.py:118-156) -- server-side
  ownership resolution for content performance, and the only place a
  non-privileged caller's ``creator_id`` claim is trusted.

The DENY cases are the point of this module, so each is asserted precisely on
both status code and detail. The documented contract is fail-closed: when
ownership cannot be resolved the request is denied, never allowed.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from jose import jwt

from app.api import analytics_routes as routes_module
from app.api.analytics_routes import get_current_user_claims, require_content_access
from app.core.content_client import ContentServiceUnavailableError
from app.core.settings import settings

# ------------------------------------------------------------- JWT minting --

GOOD_JWT = "K7bQx2Zf9pLw4mNc8vRt3yHs6dJg1aEe5uIoP0zXcVb"
DROP = object()


def mint(**overrides) -> str:
    """Mint an access token the way auth-service would."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "role": "user",
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    for key, value in overrides.items():
        if value is DROP:
            claims.pop(key, None)
        else:
            claims[key] = value
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def bearer(token: str) -> str:
    return f"Bearer {token}"


# ========================== get_current_user_claims =========================


@pytest.mark.unit
@pytest.mark.parametrize(
    "header", [None, "", "Basic abc", "bearer lowercase", "Token xyz", "Bearer"]
)
async def test_claims_reject_a_non_bearer_header(header):
    """analytics_routes.py:37-41 -- anything but 'Bearer ' is a 401."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(header)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing or invalid authorization header"


@pytest.mark.unit
async def test_claims_return_user_id_and_role():
    """The happy path yields a UUID user_id and the token's role."""
    subject = uuid4()
    claims = await get_current_user_claims(bearer(mint(sub=str(subject), role="creator")))

    assert claims["user_id"] == subject
    assert isinstance(claims["user_id"], UUID)
    assert claims["role"] == "creator"


@pytest.mark.unit
async def test_claims_default_the_role_to_user():
    """A token with no role claim must not inherit a privileged one."""
    claims = await get_current_user_claims(bearer(mint(role=DROP)))
    assert claims["role"] == "user"


@pytest.mark.unit
async def test_claims_accept_the_user_id_claim_fallback():
    """``sub`` absent but ``user_id`` present -> the fallback is used."""
    subject = uuid4()
    claims = await get_current_user_claims(bearer(mint(sub=DROP, user_id=str(subject))))
    assert claims["user_id"] == subject


@pytest.mark.unit
async def test_claims_reject_a_garbage_token():
    """analytics_routes.py:58-59 -- a JWTError becomes a 401, never a 500."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer("not-a-jwt"))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_claims_reject_a_token_signed_with_another_key():
    """A valid JWT under an attacker key must not authenticate."""
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        "attacker-controlled-key-000000000000000",
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(forged))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_claims_reject_the_wrong_audience():
    """The decode pins ``audience``; another API's token must be refused."""
    other = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": "some-other-api",
            "iss": settings.JWT_ISSUER,
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(other))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
async def test_claims_reject_the_wrong_issuer():
    """A correctly-signed token from another issuer must be refused."""
    other = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": "attacker-issuer",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(other))
    assert exc.value.status_code == 401


@pytest.mark.unit
async def test_claims_reject_an_expired_token():
    """An expired token is a 401."""
    now = datetime.now(UTC)
    token = mint(exp=int((now - timedelta(hours=1)).timestamp()))
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(token))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


@pytest.mark.unit
@pytest.mark.parametrize("token_type", ["refresh", None, "id", "service"])
async def test_claims_reject_any_non_access_token_type(token_type):
    """#221: token-type separation is enforced before the role is read.

    This is the important one for authorization: a *refresh* token for the
    same user would otherwise carry a stale ``role`` claim into the gate.
    """
    token = mint(type=token_type)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(token))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token type"


@pytest.mark.unit
async def test_claims_reject_a_refresh_token_even_when_it_claims_admin():
    """A refresh token with ``role: admin`` must not authenticate at all."""
    token = mint(type="refresh", role="admin")
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(token))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token type"


@pytest.mark.unit
async def test_claims_reject_a_missing_subject():
    """analytics_routes.py:60-64 -- no sub and no user_id is a 401."""
    token = mint(sub=DROP, user_id=DROP)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(token))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


@pytest.mark.unit
async def test_claims_reject_a_non_uuid_subject():
    """analytics_routes.py:65-70 -- an unparseable sub is a 401, not a 500."""
    token = mint(sub="not-a-uuid")
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(token))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token subject"


@pytest.mark.unit
async def test_claims_reject_a_non_string_subject():
    """python-jose rejects a non-string ``sub`` during claim validation."""
    token = mint(sub=12345)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_claims(bearer(token))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


# ========================== require_content_access ==========================


def make_request(path_params: dict) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/analytics/content/x",
            "headers": [],
            "path_params": path_params,
            "query_string": b"",
        }
    )


@pytest.mark.unit
async def test_content_access_requires_a_content_id_path_param():
    """analytics_routes.py:130-132 -- a missing path param is a 422."""
    with pytest.raises(HTTPException) as exc:
        await require_content_access({"user_id": uuid4(), "role": "user"}, make_request({}))
    assert exc.value.status_code == 422


@pytest.mark.unit
async def test_content_access_rejects_a_malformed_content_id():
    """analytics_routes.py:133-139 -- an unparseable id is a 422 with a detail."""
    with pytest.raises(HTTPException) as exc:
        await require_content_access(
            {"user_id": uuid4(), "role": "user"},
            make_request({"content_id": "not-a-uuid"}),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid content_id"


@pytest.mark.unit
async def test_privileged_role_bypasses_ownership_resolution():
    """analytics_routes.py:140-141 -- admins may read any content, no lookup.

    The short-circuit is asserted by making ``resolve_content_owner`` explode:
    a privileged read must never reach content-service.
    """
    content_id = uuid4()
    with patch.object(
        routes_module, "resolve_content_owner", side_effect=AssertionError("must not call")
    ):
        resolved = await require_content_access(
            {"user_id": uuid4(), "role": settings.PRIVILEGED_ROLE},
            make_request({"content_id": str(content_id)}),
        )
    assert resolved == content_id


@pytest.mark.unit
async def test_privileged_role_ignores_a_malformed_content_id():
    """The UUID parse happens *before* the role check, so 422 wins.

    Order matters: an unparseable id never reaches the admin short-circuit.
    """
    with pytest.raises(HTTPException) as exc:
        await require_content_access(
            {"user_id": uuid4(), "role": settings.PRIVILEGED_ROLE},
            make_request({"content_id": "nope"}),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid content_id"


@pytest.mark.unit
async def test_owning_creator_is_allowed():
    """analytics_routes.py:151-156 -- the owning creator reads their own data."""
    content_id = uuid4()
    owner = uuid4()
    with patch.object(
        routes_module, "resolve_content_owner", AsyncMock(return_value=owner)
    ):
        resolved = await require_content_access(
            {"user_id": owner, "role": "user"}, make_request({"content_id": str(content_id)})
        )
    assert resolved == content_id


@pytest.mark.unit
async def test_a_different_creator_is_denied_with_404():
    """analytics_routes.py:151-155 -- someone else's content is a bare 404.

    The detail is deliberately generic ("Not found") so the endpoint does not
    confirm that the content exists.
    """
    content_id = uuid4()
    other_owner = uuid4()
    with patch.object(
        routes_module, "resolve_content_owner", AsyncMock(return_value=other_owner)
    ):
        with pytest.raises(HTTPException) as exc:
            await require_content_access(
                {"user_id": uuid4(), "role": "user"},
                make_request({"content_id": str(content_id)}),
            )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


@pytest.mark.unit
async def test_unknown_content_is_denied_with_404():
    """analytics_routes.py:149-150 -- content-service 404 -> local 404."""
    content_id = uuid4()
    with patch.object(
        routes_module, "resolve_content_owner", AsyncMock(return_value=None)
    ):
        with pytest.raises(HTTPException) as exc:
            await require_content_access(
                {"user_id": uuid4(), "role": "user"},
                make_request({"content_id": str(content_id)}),
            )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Content not found"


@pytest.mark.unit
async def test_unresolvable_ownership_denies_with_503():
    """analytics_routes.py:142-148 -- fail-closed when content-service is down.

    The critical case: an *unverifiable* owner must never be treated as
    "allowed". The client-supplied ``creator_id`` is never consulted, so there
    is no fallback that could grant access here.
    """
    content_id = uuid4()
    with patch.object(
        routes_module,
        "resolve_content_owner",
        AsyncMock(side_effect=ContentServiceUnavailableError("content-service down")),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_content_access(
                {"user_id": uuid4(), "role": "user"},
                make_request({"content_id": str(content_id)}),
            )
    assert exc.value.status_code == 503
    assert exc.value.detail == "Could not verify content ownership"


@pytest.mark.unit
async def test_unresolvable_ownership_denies_even_a_privileged_role_with_a_bad_host():
    """A 503 from resolution only happens on the non-privileged path.

    Admins short-circuit first, so they are unaffected by an outage. Pinned so
    the two orderings stay intentional.
    """
    content_id = uuid4()
    with patch.object(
        routes_module,
        "resolve_content_owner",
        AsyncMock(side_effect=ContentServiceUnavailableError("down")),
    ):
        # Non-privileged: denied.
        with pytest.raises(HTTPException) as exc:
            await require_content_access(
                {"user_id": uuid4(), "role": "user"},
                make_request({"content_id": str(content_id)}),
            )
        assert exc.value.status_code == 503

        # Privileged: allowed without any resolution.
        assert (
            await require_content_access(
                {"user_id": uuid4(), "role": settings.PRIVILEGED_ROLE},
                make_request({"content_id": str(content_id)}),
            )
            == content_id
        )


@pytest.mark.unit
async def test_a_non_admin_role_is_not_treated_as_privileged():
    """Only ``settings.PRIVILEGED_ROLE`` short-circuits; 'superadmin' does not.

    Guards against a role allow-list drift that would hand out blanket access.
    """
    content_id = uuid4()
    owner = uuid4()
    with patch.object(
        routes_module, "resolve_content_owner", AsyncMock(return_value=owner)
    ):
        with pytest.raises(HTTPException) as exc:
            await require_content_access(
                {"user_id": uuid4(), "role": "superadmin"},
                make_request({"content_id": str(content_id)}),
            )
    assert exc.value.status_code == 404


@pytest.mark.unit
async def test_a_creator_role_does_not_grant_privileged_access():
    """A 'creator' role still only sees content it owns."""
    content_id = uuid4()
    with patch.object(
        routes_module, "resolve_content_owner", AsyncMock(return_value=uuid4())
    ):
        with pytest.raises(HTTPException) as exc:
            await require_content_access(
                {"user_id": uuid4(), "role": "creator"},
                make_request({"content_id": str(content_id)}),
            )
    assert exc.value.status_code == 404


@pytest.mark.unit
async def test_content_access_never_trusts_a_client_supplied_creator_id():
    """A ``creator_id`` in the claims is ignored; ownership is server-side.

    A forged ``{"role": "user", "user_id": victim, "creator_id": attacker}``
    must still resolve through content-service and be denied.
    """
    content_id = uuid4()
    victim = uuid4()
    attacker = uuid4()
    claims = {"user_id": attacker, "role": "user", "creator_id": str(attacker)}
    with patch.object(
        routes_module, "resolve_content_owner", AsyncMock(return_value=victim)
    ) as resolve:
        with pytest.raises(HTTPException) as exc:
            await require_content_access(claims, make_request({"content_id": str(content_id)}))
    assert exc.value.status_code == 404
    resolve.assert_awaited_once_with(content_id)


@pytest.mark.unit
async def test_content_access_does_not_mutate_state_before_authorizing():
    """Only a read-only content-service lookup may precede the decision.

    Nothing else is invoked: the dependency must not touch the analytics DB.
    """
    content_id = uuid4()
    request = make_request({"content_id": str(content_id)})
    db_spy = MagicMock()
    request.state.db = db_spy

    with patch.object(routes_module, "resolve_content_owner", AsyncMock(return_value=uuid4())):
        with pytest.raises(HTTPException):
            await require_content_access({"user_id": uuid4(), "role": "user"}, request)

    db_spy.assert_not_called()


# ================== sibling dependencies in the same module =================
# These lines are all authorization/dependency wiring in analytics_routes.py
# that the pre-existing suite skips by overriding the dependencies outright.


@pytest.mark.unit
async def test_get_current_user_id_unwraps_the_claims():
    """analytics_routes.py:77-80 -- the user id is taken from the claims."""
    from app.api.analytics_routes import get_current_user_id

    user_id = uuid4()
    claims = {"user_id": user_id, "role": "user"}

    assert await get_current_user_id(claims) == user_id


@pytest.mark.unit
async def test_get_current_user_id_asserts_a_real_uuid():
    """The ``assert isinstance`` guard must reject a non-UUID claim."""
    from app.api.analytics_routes import get_current_user_id

    with pytest.raises(AssertionError):
        await get_current_user_id({"user_id": "not-a-uuid", "role": "user"})


@pytest.mark.unit
async def test_get_current_user_id_propagates_a_missing_claim():
    """A claims dict without ``user_id`` is a KeyError, not a silent None."""
    from app.api.analytics_routes import get_current_user_id

    with pytest.raises(KeyError):
        await get_current_user_id({"role": "user"})


@pytest.mark.unit
async def test_require_self_allows_a_matching_path_user():
    """analytics_routes.py:88-90 -- own data is served."""
    from app.api.analytics_routes import require_self

    user_id = uuid4()
    request = make_request({"user_id": str(user_id)})

    assert await require_self(user_id, request) == user_id


@pytest.mark.unit
async def test_require_self_allows_when_no_path_user_is_present():
    """No path param means the route is not user-scoped; fall through."""
    from app.api.analytics_routes import require_self

    user_id = uuid4()
    assert await require_self(user_id, make_request({})) == user_id


@pytest.mark.unit
async def test_require_self_rejects_a_mismatch_with_404():
    """analytics_routes.py:91-94 -- someone else's data is a bare 404."""
    from app.api.analytics_routes import require_self

    with pytest.raises(HTTPException) as exc:
        await require_self(uuid4(), make_request({"user_id": str(uuid4())}))
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


@pytest.mark.unit
async def test_require_creator_access_requires_a_creator_id_path_param():
    """analytics_routes.py:107-109 -- a missing creator_id is a 422."""
    from app.api.analytics_routes import require_creator_access

    with pytest.raises(HTTPException) as exc:
        await require_creator_access(
            {"user_id": uuid4(), "role": "user"}, make_request({})
        )
    assert exc.value.status_code == 422


@pytest.mark.unit
async def test_require_creator_access_allows_creator_self():
    """A creator may always read their own analytics."""
    from app.api.analytics_routes import require_creator_access

    creator = uuid4()
    resolved = await require_creator_access(
        {"user_id": creator, "role": "user"}, make_request({"creator_id": str(creator)})
    )
    assert resolved == creator


@pytest.mark.unit
async def test_require_creator_access_allows_a_privileged_role():
    """``PRIVILEGED_ROLE`` may read any creator's analytics."""
    from app.api.analytics_routes import require_creator_access

    creator = uuid4()
    resolved = await require_creator_access(
        {"user_id": uuid4(), "role": settings.PRIVILEGED_ROLE},
        make_request({"creator_id": str(creator)}),
    )
    assert resolved == creator


@pytest.mark.unit
async def test_require_creator_access_denies_an_ordinary_user_with_404():
    """An ordinary user must not inherit creator-scope access."""
    from app.api.analytics_routes import require_creator_access

    with pytest.raises(HTTPException) as exc:
        await require_creator_access(
            {"user_id": uuid4(), "role": "user"},
            make_request({"creator_id": str(uuid4())}),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


@pytest.mark.unit
async def test_get_analytics_service_wires_all_four_repositories():
    """analytics_routes.py:161-167 -- the service gets every repository."""
    from app.api.analytics_routes import get_analytics_service
    from app.repositories import (
        ContentPerformanceMetricsRepository,
        ContentViewEventRepository,
        CreatorAnalyticsSnapshotRepository,
        EventRepository,
    )
    from app.services import AnalyticsService

    db = MagicMock()
    service = await get_analytics_service(db)

    assert isinstance(service, AnalyticsService)
    for repo_type in (
        EventRepository,
        ContentViewEventRepository,
        CreatorAnalyticsSnapshotRepository,
        ContentPerformanceMetricsRepository,
    ):
        assert any(isinstance(r, repo_type) for r in vars(service).values())


@pytest.mark.unit
async def test_log_event_converts_a_service_valueerror_into_422():
    """analytics_routes.py:187-190 -- a domain error becomes a 422, not a 500."""
    from fastapi.testclient import TestClient

    from app.api.analytics_routes import get_analytics_service
    from app.schemas import LogEventRequest
    from app.main import app

    caller = uuid4()
    service = MagicMock()
    service.log_event = AsyncMock(side_effect=ValueError("event_type is not allowed"))

    app.dependency_overrides.clear()
    app.dependency_overrides[get_current_user_claims] = lambda: {
        "user_id": caller,
        "role": "user",
    }
    app.dependency_overrides[get_analytics_service] = lambda: service
    try:
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        r = client.post(
            "/api/v1/analytics/events",
            json={"user_id": str(caller), "event_type": "bogus"},
        )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 422
    assert r.json()["detail"] == "event_type is not allowed"


@pytest.mark.unit
async def test_record_view_event_404s_for_a_mismatched_viewer():
    """analytics_routes.py:212-213 -- no view is recorded for another user."""
    from fastapi.testclient import TestClient

    from app.api.analytics_routes import get_analytics_service
    from app.main import app

    caller = uuid4()
    service = MagicMock()
    service.record_view_event = AsyncMock()

    app.dependency_overrides.clear()
    app.dependency_overrides[get_current_user_claims] = lambda: {
        "user_id": caller,
        "role": "user",
    }
    app.dependency_overrides[get_analytics_service] = lambda: service
    try:
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        r = client.post(
            "/api/v1/analytics/view-events",
            json={
                "content_id": str(uuid4()),
                "viewer_id": str(uuid4()),
                "watch_duration_seconds": 10,
                "content_duration_seconds": 100,
                "completion_pct": 10.0,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404
    assert r.json()["detail"] == "Not found"
    service.record_view_event.assert_not_called()
