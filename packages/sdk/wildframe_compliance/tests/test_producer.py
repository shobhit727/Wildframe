"""Tests for producer - PolicyChangeProducer."""

import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from wildframe_compliance.events import ComplianceEventType
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.producer import PolicyChangeProducer
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.policy import GDPRPolicy


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
async def test_producer_init():
    settings = MockSettings()
    producer = PolicyChangeProducer(settings)
    assert producer.settings == settings
    assert producer._publisher is None


@pytest.mark.asyncio
async def test_producer_start_stop():
    settings = MockSettings()
    producer = PolicyChangeProducer(settings)
    with patch("wildframe_compliance.producer.KafkaEventPublisher") as MockPub:
        mock_pub = AsyncMock()
        MockPub.return_value = mock_pub
        await producer.start()
        assert producer._publisher is not None
        await producer.stop()
        assert producer._publisher is None


@pytest.mark.asyncio
async def test_producer_publish_policy_change():
    settings = MockSettings()
    producer = PolicyChangeProducer(settings)
    producer._publisher = AsyncMock()
    producer._publisher.publish = AsyncMock()
    policy = GDPRPolicy()
    await producer.publish_policy_change(ComplianceEventType.POLICY_CREATED, policy)
    assert producer._publisher.publish.called
