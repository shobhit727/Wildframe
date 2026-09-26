import pytest
import pytest_asyncio
import pathlib
import importlib.util
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


# Load admin-service models and repositories
def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"Cannot load module {name} from {path}")
    spec.loader.exec_module(mod)
    return mod


models_path = pathlib.Path(__file__).parents[2] / "app" / "models" / "__init__.py"
repos_path = pathlib.Path(__file__).parents[2] / "app" / "repositories" / "__init__.py"

_models_mod = _load_module("admin_models", models_path)
_repos_mod = _load_module("admin_repos", repos_path)

Base = _models_mod.Base
UserModeration = _models_mod.UserModeration
ContentModeration = _models_mod.ContentModeration
SystemAlert = _models_mod.SystemAlert
SystemConfig = _models_mod.SystemConfig
AdminAuditLog = _models_mod.AdminAuditLog

UserModerationRepository = _repos_mod.UserModerationRepository
ContentModerationRepository = _repos_mod.ContentModerationRepository
SystemAlertRepository = _repos_mod.SystemAlertRepository
SystemConfigRepository = _repos_mod.SystemConfigRepository
AdminAuditLogRepository = _repos_mod.AdminAuditLogRepository


@pytest_asyncio.fixture
async def db_session(tmp_path):
    """Create a fresh async SQLite DB per test file."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'test.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


# ---------- UserModerationRepository ----------
@pytest.mark.asyncio
async def test_user_moderation_crud(db_session: AsyncSession):
    repo = UserModerationRepository(db_session)
    await repo.create(user_id="user1", status="active", reason=None, moderated_by="admin")
    fetched = await repo.get_by_user_id("user1")
    assert fetched and fetched.status == "active"
    await repo.update_status("user1", "banned", "spam", "admin2")
    updated = await repo.get_by_user_id("user1")
    assert updated and updated.status == "banned" and updated.reason == "spam"
    await repo.list_moderated_users()


# ---------- ContentModerationRepository ----------
@pytest.mark.asyncio
async def test_content_moderation_crud(db_session: AsyncSession):
    repo = ContentModerationRepository(db_session)
    cm = await repo.create(
        content_id="c1",
        content_type="video",
        status="flagged",
        reason="offensive",
        flagged_by="mod",
    )
    fetched = await repo.get_by_id(cm.id)
    assert fetched and fetched.status == "flagged"
    await repo.update_status("c1", "removed", "mod2")
    updated = await repo.get_by_id(cm.id)
    assert updated and updated.status == "removed"
    active = await repo.get_active_flag("c1", "mod")
    assert active is None
    await repo.list_by_status("removed")


# ---------- SystemAlertRepository ----------
@pytest.mark.asyncio
async def test_system_alert_crud(db_session: AsyncSession):
    repo = SystemAlertRepository(db_session)
    alert = await repo.create(
        alert_type="error", severity="high", message="disk full", service="admin"
    )
    fetched = await repo.get_by_id(alert.id)
    assert fetched and fetched.severity == "high"
    unack = await repo.list_unacknowledged()
    assert any(a.id == alert.id for a in unack)
    ack = await repo.acknowledge(alert.id, admin_id="admin")
    assert ack and ack.acknowledged


# ---------- SystemConfigRepository ----------
@pytest.mark.asyncio
async def test_system_config_crud(db_session: AsyncSession):
    repo = SystemConfigRepository(db_session)
    await repo.create(
        key="feature_x", value="on", config_type="bool", description=None, updated_by="admin"
    )
    fetched = await repo.get_by_key("feature_x")
    assert fetched and fetched.value == "on"
    await repo.update("feature_x", "off", "admin2", "bool", None)
    updated = await repo.get_by_key("feature_x")
    assert updated and updated.value == "off"
    all_cfg = await repo.list_all()
    assert any(c.key == "feature_x" for c in all_cfg)


# ---------- AdminAuditLogRepository (append‑only) ----------
@pytest.mark.asyncio
async def test_admin_audit_log_append_only(db_session: AsyncSession):
    repo = AdminAuditLogRepository(db_session)
    log = await repo.create(
        admin_id="admin",
        action="login",
        resource_type="session",
        resource_id="s1",
        changes=None,
        ip_address="127.0.0.1",
    )
    fetched = await repo.list_by_admin("admin")
    assert any(entry.id == log.id for entry in fetched)
    with pytest.raises(Exception):
        await repo.update()
    with pytest.raises(Exception):
        await repo.delete()
