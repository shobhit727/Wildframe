import pathlib
from fastapi import FastAPI
import warnings


def test_dead_model_fields_removed():
    text = pathlib.Path("app/models/__init__.py").read_text()
    assert "email_verification_code" not in text
    assert "email_verification_token_jti" not in text
    assert "backup_codes" not in text


def test_dead_service_methods_removed():
    text = pathlib.Path("app/services/__init__.py").read_text()
    assert "def send_email_verification" not in text
    assert "def verify_email" not in text
    assert "import secrets" not in text


def test_dead_schema_removed():
    text = pathlib.Path("app/schemas/__init__.py").read_text()
    assert "class MFASetupRequest" not in text


def test_dead_repo_file_gone():
    assert not pathlib.Path("app/repositories/user_repository.py").exists()


def test_duplicate_routes_removed():
    text = pathlib.Path("app/api/routes/__init__.py").read_text()
    assert text.count("router.include_router") == 2
    assert "def register" not in text
    assert "get_current_user_id" not in text


def test_canonical_models_and_services():
    from app.models import User
    from app.services import AuthService
    from app.security import TokenManager, SecretCipher

    assert hasattr(User, "email_verified")
    assert not hasattr(User, "backup_codes")
    assert "backup_codes" not in User.__table__.columns
    assert "email_verification_code" not in User.__table__.columns
    assert not hasattr(AuthService, "send_email_verification")
    assert not hasattr(AuthService, "verify_email")
    assert hasattr(TokenManager, "create_email_verification_token")
    assert hasattr(SecretCipher, "encrypt")


def test_no_duplicate_operation_ids():
    from app.api.routes import router as api_router

    app = FastAPI()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        app.include_router(api_router, prefix="/api/v1")
        app.openapi()
        dups = [x for x in w if "Duplicate Operation ID" in str(x.message)]
        assert len(dups) == 0
    paths = sorted(app.openapi()["paths"].keys())
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/verify-email" in paths
