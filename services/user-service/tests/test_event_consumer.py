"""Tests for `app.core.event_consumer.run_user_registered_consumer`.

The long-running Kafka loop had no coverage at all (22 missed lines). The
contract that matters is *resilience*: a single bad message, or a broker that
goes away, must never crash the consumer task, and offsets must still be
committed so the message is not redelivered forever.

`aiokafka.AIOKafkaConsumer` is replaced with a scripted fake, so no broker and
no network are involved.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.event_consumer import (
    CONSUMER_GROUP,
    USER_REGISTERED_TOPIC,
    run_user_registered_consumer,
)

USER_ID = "11111111-2222-3333-4444-555555555555"


def _msg(payload: dict | None = None, raw: str | None = None):
    msg = MagicMock()
    msg.value = raw.encode() if raw is not None else json.dumps(payload).encode()
    return msg


class _FakeConsumer:
    """Minimal AIOKafkaConsumer stand-in driven by a fixed message list."""

    instances: list["_FakeConsumer"] = []

    def __init__(self, *args, messages=None, start_exc=None, stop_exc=None, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self._messages = list(messages or [])
        self._start_exc = start_exc
        self._stop_exc = stop_exc
        self.started = False
        self.stopped = False
        self.commits = 0
        _FakeConsumer.instances.append(self)

    async def start(self):
        if self._start_exc is not None:
            raise self._start_exc
        self.started = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def commit(self):
        self.commits += 1

    async def stop(self):
        if self._stop_exc is not None:
            raise self._stop_exc
        self.stopped = True


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeConsumer.instances = []
    yield
    _FakeConsumer.instances = []


async def _run(monkeypatch_messages=None, **consumer_kwargs):
    """Run the consumer against one scripted fake and return that fake."""
    with patch("aiokafka.AIOKafkaConsumer") as factory:
        factory.side_effect = lambda *a, **k: _FakeConsumer(
            *a, **consumer_kwargs, **k
        )
        await run_user_registered_consumer(MagicMock())
    assert factory.call_count == 1
    return _FakeConsumer.instances[-1]


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


async def test_consumer_subscribes_to_the_registration_topic_with_a_stable_group():
    with patch("aiokafka.AIOKafkaConsumer") as factory:
        factory.side_effect = lambda *a, **k: _FakeConsumer(*a, **k)
        await run_user_registered_consumer(MagicMock())

    _, kwargs = factory.call_args
    assert factory.call_args.args[0] == USER_REGISTERED_TOPIC
    assert kwargs["group_id"] == CONSUMER_GROUP
    # Manual commits: at-least-once with a real ack per message.
    assert kwargs["enable_auto_commit"] is False
    assert kwargs["auto_offset_reset"] == "earliest"
    assert "bootstrap_servers" in kwargs


async def test_bootstrap_servers_come_from_the_environment():
    with patch.dict("os.environ", {"KAFKA_BOOTSTRAP_SERVERS": "kafka.test:19092"}):
        with patch("aiokafka.AIOKafkaConsumer") as factory:
            factory.side_effect = lambda *a, **k: _FakeConsumer(*a, **k)
            await run_user_registered_consumer(MagicMock())

    assert factory.call_args.kwargs["bootstrap_servers"] == "kafka.test:19092"


async def test_bootstrap_servers_fall_back_when_the_env_var_is_absent():
    with patch.dict("os.environ", {}, clear=True):
        with patch("aiokafka.AIOKafkaConsumer") as factory:
            factory.side_effect = lambda *a, **k: _FakeConsumer(*a, **k)
            await run_user_registered_consumer(MagicMock())

    assert factory.call_args.kwargs["bootstrap_servers"] == "kafka:29092"


# ---------------------------------------------------------------------------
# message loop
# ---------------------------------------------------------------------------


async def test_a_well_formed_envelope_message_is_provisioned_and_committed():
    message = _msg({"event_id": "e1", "topic": USER_REGISTERED_TOPIC, "payload": {"user_id": USER_ID}})

    with patch("app.core.event_consumer._provision_profile", new=AsyncMock()) as provision:
        consumer = await _run(messages=[message])

    provision.assert_awaited_once()
    assert provision.await_args.args[1] == USER_ID
    assert consumer.started is True
    assert consumer.commits == 1
    assert consumer.stopped is True


async def test_a_bare_message_without_an_envelope_is_also_accepted():
    with patch("app.core.event_consumer._provision_profile", new=AsyncMock()) as provision:
        await _run(messages=[_msg({"user_id": USER_ID})])

    assert provision.await_args.args[1] == USER_ID


async def test_a_message_without_a_user_id_is_skipped_but_still_committed():
    """A poison message must not wedge the partition, and must be acked."""
    with patch("app.core.event_consumer._provision_profile", new=AsyncMock()) as provision:
        consumer = await _run(messages=[_msg({"topic": USER_REGISTERED_TOPIC})])

    provision.assert_not_awaited()
    # `finally` commits regardless, so the poison message is not replayed.
    assert consumer.commits == 1


async def test_undecodable_json_is_skipped_but_still_committed():
    with patch("app.core.event_consumer._provision_profile", new=AsyncMock()) as provision:
        consumer = await _run(messages=[_msg(raw="{not json")])

    provision.assert_not_awaited()
    assert consumer.commits == 1


async def test_a_provisioning_failure_does_not_kill_the_loop():
    good = _msg({"payload": {"user_id": USER_ID}})

    with patch(
        "app.core.event_consumer._provision_profile",
        new=AsyncMock(side_effect=[RuntimeError("profile exists"), None]),
    ) as provision:
        consumer = await _run(messages=[good, _msg({"payload": {"user_id": USER_ID}})])

    assert provision.await_count == 2
    # Both offsets acked even though the first message failed.
    assert consumer.commits == 2
    assert consumer.stopped is True


# ---------------------------------------------------------------------------
# broker failures
# ---------------------------------------------------------------------------


async def test_a_broker_that_refuses_to_start_is_logged_and_the_task_returns():
    consumer = await _run(start_exc=ConnectionRefusedError("no broker"))

    assert consumer.started is False
    # stop() is still attempted in the finally block.
    assert consumer.stopped is True


async def test_a_stop_failure_is_swallowed():
    consumer = await _run(stop_exc=RuntimeError("already closed"))

    # The exception is swallowed so shutdown never masks the real reason.
    assert consumer.stopped is False


async def test_a_loop_error_is_swallowed():
    class _Exploding(_FakeConsumer):
        async def __anext__(self):
            raise RuntimeError("partition revoked")

    with patch("aiokafka.AIOKafkaConsumer") as factory:
        factory.side_effect = lambda *a, **k: _Exploding(*a, **k)
        await run_user_registered_consumer(MagicMock())

    assert factory.call_count == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_exc": ConnectionRefusedError("x"), "stop_exc": RuntimeError("y")},
    ],
)
async def test_start_and_stop_can_both_fail_without_propagating(kwargs):
    consumer = await _run(**kwargs)

    assert consumer.started is False
