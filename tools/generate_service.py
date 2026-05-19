#!/usr/bin/env python3
"""
Service template generator for Wildframe platform.
Creates new microservices with standard directory structure and boilerplate code.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional
import shutil


# Service template files
SERVICE_TEMPLATES = {
    "pyproject.toml": '''[build-system]
requires = ["poetry-core>=1.7.0"]
build-backend = "poetry.core.masonry.api"

[project]
name = "wildframe-{service_name}-service"
version = "1.0.0"
description = "{service_description}"

[tool.poetry]
name = "wildframe-{service_name}-service"
version = "1.0.0"
description = "{service_description}"
authors = ["Wildframe Team <engineering@wildframe.com>"]
license = "Proprietary"
packages = [{{include = "app"}}]

[tool.poetry.dependencies]
python = "^3.11"
fastapi = "^0.104.0"
uvicorn = {{extras = ["standard"], version = "^0.24.0"}}
pydantic = "^2.4.0"
pydantic-settings = "^2.0.0"
sqlalchemy = "^2.0.0"
alembic = "^1.12.0"
asyncpg = "^0.29.0"
redis = "^5.0.0"
aiokafka = "^0.10.0"
python-json-logger = "^2.0.0"
httpx = "^0.25.0"
opentelemetry-api = "^1.20.0"
opentelemetry-sdk = "^1.20.0"
opentelemetry-exporter-jaeger = "^1.20.0"
prometheus-client = "^0.18.0"

[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"
pytest-asyncio = "^0.21.0"
pytest-cov = "^4.1.0"
black = "^23.11.0"
isort = "^5.12.0"
mypy = "^1.7.0"

[tool.black]
line-length = 100
target-version = ["py311"]

[tool.isort]
profile = "black"
line_length = 100
''',
    "Dockerfile": '''# Multi-stage Dockerfile for {service_name} service

FROM python:3.11-slim as builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY services/{service_name}/pyproject.toml .
RUN pip install --user --no-cache-dir poetry && poetry config virtualenvs.create false && poetry install --no-dev --no-interaction

FROM python:3.11-slim as production
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY services/{service_name} /app
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app && mkdir -p logs
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -f http://localhost:8000/health || exit 1
EXPOSE 8000
CMD ["gunicorn", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "app.main:app"]
''',
    "app/__init__.py": '"""Application module."""\n',
    "app/main.py": '''"""Main FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.settings import settings
from app.core.logging import setup_logging
from app.core.database import DatabaseManager

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield
    await DatabaseManager.close()

app = FastAPI(
    title=settings.SERVICE_NAME,
    version=settings.SERVICE_VERSION,
    lifespan=lifespan,
)

@app.get("/health")
async def health_check():
    return {{"status": "healthy", "service": settings.SERVICE_NAME}}
''',
    "app/core/__init__.py": '"""Core module."""\n',
    "app/core/settings.py": '''"""Service configuration."""
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SERVICE_NAME: str = "{service_name}-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/db"
    REDIS_URL: str = "redis://localhost:6379/0"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    LOG_LEVEL: str = "INFO"

settings = Settings()
''',
    "app/core/database.py": '''"""Database configuration."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.settings import settings

class DatabaseManager:
    _engine = None
    _session_factory = None
    
    @classmethod
    def get_engine(cls):
        if cls._engine is None:
            cls._engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
        return cls._engine
    
    @classmethod
    def get_session_factory(cls):
        if cls._session_factory is None:
            cls._session_factory = async_sessionmaker(cls.get_engine(), class_=AsyncSession)
        return cls._session_factory
    
    @classmethod
    async def close(cls):
        if cls._engine is not None:
            await cls._engine.dispose()
''',
    "app/core/logging.py": '''"""Logging configuration."""
import logging

def setup_logging():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("app").setLevel(logging.INFO)
''',
    "tests/__init__.py": '"""Tests module."""\n',
    "tests/conftest.py": '''"""Pytest configuration."""
import pytest

@pytest.fixture
async def db_session():
    """Fixture for database session."""
    # TODO: Implement test database setup
    pass
''',
    ".env.example": '''SERVICE_NAME={service_name}-service
ENVIRONMENT=development
DEBUG=True
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/{service_name}_db
REDIS_URL=redis://localhost:6379/0
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
LOG_LEVEL=DEBUG
''',
}


def create_service(service_name: str, description: Optional[str] = None) -> bool:
    """Create a new microservice with template structure.
    
    Args:
        service_name: Name of the service (e.g., 'my-service')
        description: Service description
    
    Returns:
        bool: True if successful
    """
    service_dir = Path("services") / service_name
    
    # Check if service already exists
    if service_dir.exists():
        print(f"❌ Service directory already exists: {service_dir}")
        return False
    
    description = description or f"{service_name.title()} Service"
    
    try:
        # Create directory structure
        directories = [
            service_dir / "app" / "core",
            service_dir / "app" / "models",
            service_dir / "app" / "repositories",
            service_dir / "app" / "services",
            service_dir / "app" / "schemas",
            service_dir / "app" / "api",
            service_dir / "app" / "middleware",
            service_dir / "app" / "telemetry",
            service_dir / "tests",
            service_dir / "migrations" / "versions",
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        
        # Create template files
        for file_path, content in SERVICE_TEMPLATES.items():
            full_path = service_dir / file_path
            
            # Substitute placeholders
            content = content.replace("{service_name}", service_name)
            content = content.replace("{service_description}", description)
            
            # Create file
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
        
        print(f"✅ Service created: {service_dir}")
        print(f"\nNext steps:")
        print(f"  1. cd services/{service_name}")
        print(f"  2. cp .env.example .env")
        print(f"  3. poetry install")
        print(f"  4. alembic init migrations")
        print(f"  5. Implement service logic")
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating service: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate a new microservice for Wildframe")
    parser.add_argument("name", help="Service name (e.g., my-service)")
    parser.add_argument("--description", "-d", help="Service description", default=None)
    
    args = parser.parse_args()
    
    success = create_service(args.name, args.description)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
