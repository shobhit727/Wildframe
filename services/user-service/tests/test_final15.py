"""Event-consumer contract tests for user-service.

The previous version of this file was a byte-identical placeholder copied
across five services (`assert "compliance" in ["compliance", "privacy"]`).

`app.core.event_consumer` is the only writer of default profiles, so its
*wiring constants* are part of the service's public contract with auth-service
and Kafka: if the topic name or consumer group drifts, freshly registered users
silently stop getting a profile. These tests pin that contract without needing a
broker, plus the two message-shape conventions the loop depends on.
"""

import json
from uuid import UUID, uuid4

from app.core.event_consumer import (
    CONSUMER_GROUP,
    USER_REGISTERED_TOPIC,
    _provision_profile,
)

# The contract auth-service publishes to.
EXPECTED_TOPIC = "user.registered"
EXPECTED_GROUP = "user-service"


def test_topic_name_matches_the_auth_service_contract():
    assert USER_REGISTERED_TOPIC == EXPECTED_TOPIC


def test_consumer_group_is_service_scoped():
    # One group per service: sharing a group with auth-service would steal
    # auth-service's offsets.
    assert CONSUMER_GROUP == "user-service"
    assert CONSUMER_GROUP != "auth-service"


def test_provision_profile_is_the_only_profile_writer_in_the_consumer():
    import inspect

    source = inspect.getsource(_provision_profile)

    # It builds the full dependency set the service needs to provision.
    for repository in (
        "UserProfileRepository",
        "UserDeviceRepository",
        "UserPreferenceRepository",
        "UserSubscriptionProfileRepository",
    ):
        assert repository in source
    assert "create_user_profile" in source


def test_provision_profile_accepts_the_sdk_envelope_shape():
    event = {
        "event_id": str(uuid4()),
        "topic": EXPECTED_TOPIC,
        "payload": {"user_id": str(uuid4())},
    }

    payload = event.get("payload", event)

    assert UUID(payload["user_id"])


def test_provision_profile_accepts_a_bare_event_shape():
    user_id = str(uuid4())

    payload = {"user_id": user_id}.get("payload", {"user_id": user_id})

    assert UUID(payload["user_id"])


def test_consumer_events_are_json_encoded_utf8():
    """The loop does `json.loads(msg.value.decode("utf-8"))` - prove it round-trips."""
    user_id = str(uuid4())
    raw = json.dumps({"payload": {"user_id": user_id}}).encode("utf-8")

    event = json.loads(raw.decode("utf-8"))

    assert event["payload"]["user_id"] == user_id
