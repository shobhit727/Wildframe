"""The production write surface of content-service is authenticated.

`app/main.py` mounts exactly one API router, `app.api.routes.router`, under
`/api/v1`. The other four -- `rights`, `dsar`, `reviews` and `ads` -- are
deliberately left unmounted. For the first three that is an unfinished
feature; for `ads` it is a **security decision**: `create_ad` declares no
authentication dependency at all, so mounting `ads.router` would publish an
unauthenticated write endpoint, and `GET /ads/check` alongside it.

That decision used to live only in a commit message and a test docstring. A
mutation report found the obvious consequence: adding one `include_router` line
to `main.py` left all 446 tests green while the production OpenAPI gained
`/ads` and `/ads/check` with nothing but a database session in front of them.
Nothing executable enforced a decision that only existed in prose.

So these tests enforce the *property* rather than the wiring:

* no write operation in the production OpenAPI is reachable without an auth
  gate (`TestNoUnauthenticatedWrites`), and
* the ads write paths specifically are never open (`TestAdsIsNeverExposed`).

Both are written against `app.openapi()` so a legitimate refactor -- moving
auth to `Security()`/`HTTPBearer` instead of an `Authorization` header
dependency, re-prefixing, or even mounting a *properly authenticated* ads
router -- keeps them green. Only an unauthenticated write fails them.

A note on the DB session: `create_ad` takes `Depends(get_db)`, which FastAPI
does not surface in the OpenAPI at all. It therefore cannot be what protects
the route, and these tests do not credit it as an auth gate.
"""

import pytest

from app.api.routes import ads as ads_routes
from app.main import create_app

pytestmark = pytest.mark.unit

#: Methods that change state. A missing gate on any of these is the hazard.
WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})

#: Production write operations known to have no auth gate. This is a record of
#: outstanding debt, not an endorsement -- shrink it, never extend it.
#:
#: `POST /api/v1/reindex` (`app/api/routes/__init__.py:658`) has no dependency
#: of any kind. It mutates the in-memory `_reindex_jobs` registry and answers
#: 200 to any caller. Gating it is a production behaviour change that needs an
#: owner decision (who may trigger a full reindex, and what the gateway should
#: see), so it is recorded here rather than fixed here. `test_routes.py`
#: exercises it unauthenticated, so adding auth today would break that suite.
KNOWN_UNAUTHENTICATED_WRITES = frozenset(
    {
        ("POST", "/api/v1/reindex"),
    }
)


def _write_operations(schema: dict) -> dict[tuple[str, str], dict]:
    """Every (METHOD, path) write operation in an OpenAPI document."""
    return {
        (method.upper(), path): operation
        for path, operations in schema.get("paths", {}).items()
        for method, operation in operations.items()
        if method.lower() in WRITE_METHODS
    }


def _has_auth_gate(operation: dict) -> bool:
    """True when an operation cannot be reached without authenticating.

    Two shapes count, because the service uses the first today and is free to
    migrate to the second:

    * ``security`` -- a declared security scheme (``HTTPBearer``, ``OAuth2``,
      or an explicit ``Security()`` dependency), or
    * an ``Authorization`` header parameter -- which is what FastAPI emits for
      the ``Header(alias="Authorization")`` dependency style this codebase uses
      (``get_admin_identity``/``get_current_user`` in ``app/api/routes``).
    """
    if operation.get("security"):
        return True
    return any(
        str(parameter.get("name", "")).lower() == "authorization"
        for parameter in operation.get("parameters") or []
    )


def _unauthenticated_writes(schema: dict) -> set[tuple[str, str]]:
    return {
        key for key, operation in _write_operations(schema).items() if not _has_auth_gate(operation)
    }


def _ads_paths() -> set[str]:
    """The paths `ads.router` declares, including its own prefix.

    Read off the router rather than hardcoded, so renaming the prefix does not
    turn this guard into a stale literal that silently passes.
    """
    return {route.path for route in ads_routes.router.routes if route.path not in ("", "/")}


class TestNoUnauthenticatedWrites:
    def test_no_write_operation_is_reachable_without_auth(self):
        """The general property: nothing state-changing is open.

        Asserted as "the open set is a subset of the known-debt set" rather
        than "the open set is empty", so the existing `/api/v1/reindex` gap
        does not have to be papered over -- but any *new* unauthenticated
        write, including a mounted `ads.router`, fails here.
        """
        open_writes = _unauthenticated_writes(create_app().openapi())

        assert open_writes <= KNOWN_UNAUTHENTICATED_WRITES, (
            "unauthenticated write operation(s) present in the production OpenAPI: "
            f"{sorted(open_writes - KNOWN_UNAUTHENTICATED_WRITES)}. Mounting a router "
            "whose handlers declare no auth dependency publishes an unauthenticated "
            "write endpoint. Add authentication to the handler, or record a "
            "deliberate exception in KNOWN_UNAUTHENTICATED_WRITES with a reason."
        )

    def test_the_guard_is_not_vacuous_there_are_writes_to_check(self):
        """A guard that finds nothing because it scanned nothing proves nothing.

        ``AGENTS.md`` 19.1: a test asserting only "the list is empty" passes
        against a collection step that never ran. Pin that the enumeration
        really sees the catalog's write surface.
        """
        writes = _write_operations(create_app().openapi())

        assert len(writes) >= 10
        assert ("POST", "/api/v1/content") in writes
        assert ("DELETE", "/api/v1/genres/{genre_id}") in writes

    def test_the_known_debt_entry_is_still_real(self):
        """Keep the allowlist honest: a closed entry should be removed.

        Without this, someone could gate `/api/v1/reindex` and the stale
        allowlist entry would keep masking a path that no longer exists.
        """
        writes = _write_operations(create_app().openapi())

        stale = {
            key
            for key in KNOWN_UNAUTHENTICATED_WRITES
            if key not in writes or _has_auth_gate(writes[key])
        }
        assert not stale, (
            f"KNOWN_UNAUTHENTICATED_WRITES lists {sorted(stale)}, which is now gone or "
            "authenticated. Remove the entry -- the allowlist should shrink."
        )


class TestAdsIsNeverExposed:
    def test_the_ads_write_paths_are_not_open_in_production(self):
        """The specific hazard, named.

        Deliberately *not* "the ads paths are absent". Mounting `ads.router`
        behind real authentication is a legitimate future change; mounting it
        unauthenticated is the defect. Only the second is rejected here, which
        is also what keeps this test from re-pinning today's wiring.
        """
        schema = create_app().openapi()
        ads_paths = _ads_paths()
        assert ads_paths, "ads.router declares no paths -- this guard would be vacuous"

        exposed = [
            (method, path)
            for (method, path) in _unauthenticated_writes(schema)
            # an ads path mounted under an extra prefix still matches here
            if any(path == ads_path or path.endswith(ads_path) for ads_path in ads_paths)
        ]

        assert not exposed, (
            f"unauthenticated ads write operation(s) exposed by the production app: "
            f"{exposed}. ads.create_ad declares no auth dependency, so mounting "
            "app.api.routes.ads.router opens a write surface to anyone who can "
            "reach the service. Gate create_ad before mounting it."
        )

    def test_premise_mounting_the_ads_router_now_would_open_a_write(self):
        """Why the guard above is needed: the handler is genuinely unprotected.

        Mounting `ads.router` in a throwaway app and running the *same*
        ``_has_auth_gate`` check the guard uses shows the hazard directly:
        every ads write operation comes out unauthenticated. It also keeps the
        guard honest in the other direction -- if `create_ad` ever gains auth,
        this fails, which is the intended prompt to reconsider the decision
        rather than let the allowlist quietly absorb it.
        """
        from fastapi import FastAPI

        throwaway = FastAPI()
        throwaway.include_router(ads_routes.router)
        schema = throwaway.openapi()

        assert "/ads" in schema["paths"], "ads.router no longer declares /ads"
        assert "/ads/check" in schema["paths"], "ads.router no longer declares /ads/check"
        writes = _write_operations(schema)
        assert ("POST", "/ads") in writes

        open_writes = _unauthenticated_writes(schema)
        assert ("POST", "/ads") in open_writes, (
            "POST /ads now has an auth gate. The premise of this file has "
            "changed: mounting ads.router may be a legitimate next step. "
            "Re-read KNOWN_UNAUTHENTICATED_WRITES and main.py."
        )
