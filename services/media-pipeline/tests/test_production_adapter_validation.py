import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def _production_kwargs(adapter: str) -> dict:
    return {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+asyncpg://user:strongpass@db.example.com:5432/media_db",
        "REDIS_URL": "redis://redis.example.com:6379",
        "JWT_SECRET_KEY": "a-very-strong-jwt-secret-key-32+chars-long!",
        "KAFKA_BOOTSTRAP_SERVERS": "kafka.example.com:9092",
        "MEDIA_PIPELINE_ADAPTERS": adapter,
    }


def test_production_stub_fails():
    with pytest.raises(ValidationError) as exc:
        Settings(**_production_kwargs("stub"))
    assert "MEDIA_PIPELINE_ADAPTERS" in str(exc.value)


def test_production_empty_fails():
    with pytest.raises(ValidationError) as exc:
        Settings(**_production_kwargs(""))
    assert "MEDIA_PIPELINE_ADAPTERS" in str(exc.value)


def test_production_none_fails():
    with pytest.raises(ValidationError) as exc:
        Settings(**_production_kwargs(None))  # type: ignore[arg-type]
    assert "MEDIA_PIPELINE_ADAPTERS" in str(exc.value)


def test_production_ffmpeg_passes():
    s = Settings(**_production_kwargs("ffmpeg"))
    assert s.MEDIA_PIPELINE_ADAPTERS == "ffmpeg"
    assert s.ENVIRONMENT == "production"


def test_development_stub_passes():
    s = Settings(ENVIRONMENT="development", MEDIA_PIPELINE_ADAPTERS="stub")
    assert s.MEDIA_PIPELINE_ADAPTERS == "stub"


def test_test_env_stub_passes():
    s = Settings(ENVIRONMENT="test", MEDIA_PIPELINE_ADAPTERS="stub")
    assert s.MEDIA_PIPELINE_ADAPTERS == "stub"
