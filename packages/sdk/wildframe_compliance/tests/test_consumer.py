"""Tests for consumer - PolicyChangeConsumer."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from wildframe_compliance.consumer import PolicyChangeConsumer, PolicyChangeHandler
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class MockSettings(ComplianceSettingsMixin):
    SERVICE_NAME: str = "test-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "test"
    DATABASE_URL: str = "sqlite:///:memory:"
    REDIS_URL: str = "redis://localhost:6379/0"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    JWT_SECRET_KEY: str = "test-secret-key-32-bytes-long-for-test"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU


@pytest.mark.asyncio
async def test_consumer_init():
    settings = MockSettings()
    consumer = PolicyChangeConsumer(settings)
    assert consumer.settings == settings
    assert consumer._running is False


@pytest.mark.asyncio
async def test_consumer_start_stop():
    settings = MockSettings()
    consumer = PolicyChangeConsumer(settings)
    with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as MockSub:
        mock_sub = AsyncMock()
        MockSub.return_value = mock_sub
        await consumer.start()
        assert consumer._running is True
        await consumer.stop()
        assert consumer._running is False


@pytest.mark.asyncio
async def test_handler_call():
    settings = MockSettings()
    handler = PolicyChangeHandler(settings)
    assert handler.settings == settings
