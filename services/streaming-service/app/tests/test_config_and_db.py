"""Regression tests for the Streaming Service startup wiring.

Covers two confirmed bugs that prevented ``streaming-service/app/main.py``
from importing:

1. ``from app.core.config import settings`` referenced ``app/core/config.py``,
   which did not exist. The real module is ``app.core.settings``.
2. ``from app.core.database import get_db_session`` referenced a helper that
   was never defined in ``core/database.py`` (only ``DatabaseManager.get_session``
   existed), so the included router failed to import.
"""

import pytest


@pytest.mark.unit
def test_settings_importable_from_core():
    """``app.core.settings.settings`` must be the source of configuration.

    ``app/main.py`` used to import from the non-existent ``app.core.config``,
    raising ``ModuleNotFoundError`` at import time; it now imports from
    ``app.core.settings``.
    """
    from app.core.settings import settings

    assert settings.settings.SERVICE_NAME == "streaming-service"
    assert isinstance(settings.settings.CORS_ALLOWED_ORIGINS, list)
    assert settings.settings.CORS_ALLOWED_ORIGINS


@pytest.mark.unit
def test_cors_attribute_matches_what_main_reads():
    """The attribute read at startup (``CORS_ALLOWED_ORIGINS``) must exist.

    ``construct_app`` reads ``settings.CORS_ALLOWED_ORIGINS``, so a mismatch
    here would raise ``AttributeError`` the instant the app is built.
    """
    from app.core.settings import settings

    assert hasattr(settings, "CORS_ALLOWED_ORIGINS")


@pytest.mark.unit
def test_get_db_session_is_async_generator():
    """``get_db_session`` must be an async generator usable by FastAPI DI.

    The included ``streaming_routes`` module imports ``get_db_session`` and
    uses it in a ``Depends(...)`` dependency, so it must be a coroutine-based
    async generator function — not an undefined name.
    """
    import inspect

    from app.core.database import get_db_session

    assert inspect.isasyncgenfunction(get_db_session), (
        "get_db_session must be an async generator function for FastAPI " "dependency injection."
    )


@pytest.mark.unit
def test_construct_app_imports_cleanly():
    """The wired ``app`` object and its router must import without errors.

    This is the end-to-end guard: importing the app module triggers the two
    historical ImportError/ModuleNotFoundError sites (config import and the
    get_db_session import inside streaming_routes).
    """
    from app import main

    assert main.app is not None

    from app.api import streaming_routes as _streaming_routes

    assert _streaming_routes.router is not None


@pytest.mark.unit
async def test_get_db_session_yields_and_closes():
    """``get_db_session`` must yield exactly one session and then close it.

    Verifies the corrected generator behaves as FastAPI expects.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.database import db_manager, get_db_session

    # Ensure the singleton has an engine to use.
    db_manager.get_session_factory()

    gen = get_db_session()
    session = await gen.asend(None)
    assert isinstance(session, AsyncSession)

    # The generator should then close the session and finish.
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)
