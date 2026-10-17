"""Behavioural tests for ``apply_dlq_retention`` — bounded DLQ retention (#553).

Mocks are mandatory here, and the reason is a real API mismatch rather than
convenience. ``dlq_retention.py`` used to call four things aiokafka does not
have:

1. ``ConfigResource(ConfigResource.Type.TOPIC, t)`` — there is no nested
   ``.Type`` enum; the topic scope is the module-level ``ConfigResourceType``.
2. ``resource.set_config(...)`` — no such method; the config map goes to the
   constructor.
3. ``await admin.alter_configs(resource)`` — it takes a *list* of resources.
4. ``(await admin.list_topics()).topics`` — it returns ``list[str]``.

Each raised ``AttributeError``/``TypeError`` inside a per-topic
``except Exception``, so the suite stayed green while retention was applied to
no topic at all. The fakes below model the surface that is actually there;
``TestInstalledAiokafkaApi`` asserts the *installed* library's own surface, so
a future aiokafka change fails here instead of in production.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any

import pytest

from wildframe_events.dlq_retention import (
    DLQ_RETENTION_MS,
    DLQ_SEGMENT_MS,
    _alter_configs_refusal,
    apply_dlq_retention,
)
from wildframe_events.topics import all_dlq_topics

#: The real aiokafka classes, captured BEFORE the autouse fixture below swaps
#: in fakes. ``TestRealAiokafkaApiMismatch`` needs the unpatched originals.
import aiokafka.admin as _real_admin  # noqa: E402
import aiokafka.admin.config_resource as _real_config_resource_module  # noqa: E402
import aiokafka as _real_aiokafka  # noqa: E402
from aiokafka.protocol.admin import AlterConfigsResponse_v0  # noqa: E402

# The real, unpatched enum. Production builds real ``ConfigResource`` objects, so
# the resource scope must be asserted against aiokafka's own enum, not a local
# stand-in that reifies an API which no longer exists.
ConfigResourceType = _real_config_resource_module.ConfigResourceType

_REAL_ADMIN_CLIENT = _real_admin.AIOKafkaAdminClient
_REAL_NEW_TOPIC = _real_admin.NewTopic
_REAL_CONFIG_RESOURCE = _real_config_resource_module.ConfigResource
_REAL_PRODUCER = _real_aiokafka.AIOKafkaProducer

#: Kafka's ``TOPIC_AUTHORIZATION_FAILED`` — an ACL denial, not a timeout.
TOPIC_AUTHORIZATION_FAILED = 29
#: ``ConfigResourceType.TOPIC``, as the wire protocol encodes it.
_RESOURCE_TYPE_TOPIC = 2


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAdmin:
    """Stand-in for ``aiokafka.admin.AIOKafkaAdminClient``."""

    instances: list["FakeAdmin"] = []

    #: Response for ``list_topics()``. Override per test.
    list_topics_result: Any = None
    #: ``alter_configs(resource)`` raises this, per topic name, when set.
    alter_fail_for: set[str] = set()
    #: ``alter_configs(resource)`` answers with a non-zero per-resource error
    #: code for these topics, as the broker does on an ACL denial.
    alter_error_for: dict[str, tuple[int, str | None]] = {}
    #: ``start()`` raises this.
    start_error: Exception | None = None
    #: ``list_topics()`` raises this.
    list_error: Exception | None = None
    #: ``create_topics`` raises this.
    create_error: Exception | None = None
    #: ``close()`` raises this.
    close_error: Exception | None = None
    #: topic name -> {config name: value} returned by ``describe_configs``.
    describe_result: dict[str, dict[str, Any]] | None = None
    #: topic names whose describe returns a non-zero error code.
    describe_error_for: set[str] = set()

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = 0
        self.stopped = 0
        self.created: list[Any] = []
        self.altered: list[Any] = []
        self.closed = 0
        type(self).instances.append(self)

    async def start(self) -> None:
        self.started += 1
        if type(self).start_error is not None:
            raise type(self).start_error

    async def list_topics(self) -> list[str]:
        """aiokafka returns a plain list of topic names, not an object."""
        if type(self).list_error is not None:
            raise type(self).list_error
        result = type(self).list_topics_result
        # aiokafka 0.14.0 returns a plain list[str]; it has no ``.topics``.
        return list(result) if result is not None else []

    async def create_topics(self, new_topics: list[Any]) -> None:
        if type(self).create_error is not None:
            raise type(self).create_error
        self.created.extend(new_topics)

    async def describe_configs(self, resources: list[Any]) -> list[Any]:
        """Return per-topic config descriptions in ``to_object()`` form."""
        described = type(self).describe_result or {}
        responses = []
        for resource in resources:
            name = resource.name
            if name in type(self).describe_error_for:
                responses.append(_DescribeResponse(name, error_code=29))
            else:
                responses.append(_DescribeResponse(name, configs=described.get(name, {})))
        return responses

    async def alter_configs(self, config_resources: list[Any]) -> Any:
        """Return the real ``AlterConfigsResponse`` aiokafka would return.

        aiokafka 0.14.0 iterates its argument, so this takes a *list* of
        resources. The body carries the outcome per resource, and a broker
        error code in it does **not** raise -- which is precisely why the caller
        has to read the response to know whether the topic was configured.
        """
        resources = list(config_resources)
        if len(resources) != 1:
            raise AssertionError(f"alter_configs takes a list; got {len(resources)} resources")
        resource = resources[0]
        name = getattr(resource, "name", None)
        if name in type(self).alter_fail_for:
            raise RuntimeError(f"alter_configs refused for {name}")
        self.altered.append(resource)
        error_code, error_message = type(self).alter_error_for.get(name, (0, None))
        return AlterConfigsResponse_v0(
            throttle_time_ms=0,
            resources=[(error_code, error_message, _RESOURCE_TYPE_TOPIC, name)],
        )

    async def close(self) -> None:
        self.closed += 1
        if type(self).close_error is not None:
            raise type(self).close_error


class FakeNewTopic:
    """Stand-in for ``aiokafka.admin.NewTopic`` with a ``topic_configs`` kwarg."""

    def __init__(
        self,
        name: str,
        num_partitions: int = 1,
        replication_factor: int = 1,
        topic_configs: dict | None = None,
    ) -> None:
        self.name = name
        self.num_partitions = num_partitions
        self.replication_factor = replication_factor
        self.topic_configs = topic_configs or {}


class LegacyNewTopic:
    """Stand-in for an older aiokafka whose kwarg is ``topic_config``.

    ``apply_dlq_retention`` probes that signature and picks whichever spelling
    the installed library accepts; only this class exercises the
    ``topic_config`` half of that ternary.
    """

    def __init__(
        self,
        name: str,
        num_partitions: int = 1,
        replication_factor: int = 1,
        topic_config: dict | None = None,
    ) -> None:
        self.name = name
        self.num_partitions = num_partitions
        self.replication_factor = replication_factor
        self.topic_config = topic_config or {}


# NOTE: ``ConfigResource`` is deliberately not faked. The stand-in that used to
# live here reimplemented ``Type.TOPIC`` and ``set_config`` — an API that does
# not exist in aiokafka 0.14.0 — and because it was patched over the real class,
# every test passed while production raised ``AttributeError`` on the first
# call. Production builds the real class, so its constructor signature is what
# the tests exercise; ``TestInstalledAiokafkaApi`` pins that surface.


class _DescribeResponse:
    """Stand-in for a describe_configs response exposing ``to_object()``.

    The keys are the ones the real ``DescribeConfigsResponse`` emits:
    ``resources[]`` of ``error_code``/``resource_name`` plus
    ``config_entries[]`` of ``config_names``/``config_value``/``read_only``.
    ``config_names`` is the wire protocol's own (plural) spelling.

    ``configs`` maps a config name to its ``(value, read_only)`` pair, where a
    ``None`` value is what the broker sends for an unset config.
    """

    def __init__(
        self,
        resource_name: str,
        configs: dict[str, Any] | None = None,
        error_code: int = 0,
    ) -> None:
        self.resource_name = resource_name
        self.configs = configs or {}
        self.error_code = error_code

    def to_object(self) -> dict:
        return {
            "resources": [
                {
                    "error_code": self.error_code,
                    "error_message": None,
                    "resource_name": self.resource_name,
                    "config_entries": [
                        {
                            "config_names": name,
                            "config_value": value,
                            "read_only": read_only,
                        }
                        for name, (value, read_only) in self.configs.items()
                    ],
                }
            ]
        }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    import aiokafka.admin

    FakeAdmin.instances = []
    FakeAdmin.list_topics_result = None
    FakeAdmin.alter_fail_for = set()
    FakeAdmin.alter_error_for = {}
    FakeAdmin.start_error = None
    FakeAdmin.list_error = None
    FakeAdmin.create_error = None
    FakeAdmin.close_error = None
    FakeAdmin.describe_result = None
    FakeAdmin.describe_error_for = set()

    monkeypatch.setattr(aiokafka.admin, "AIOKafkaAdminClient", FakeAdmin)
    monkeypatch.setattr(aiokafka.admin, "NewTopic", FakeNewTopic)
    # NOTE: aiokafka.admin.config_resource.ConfigResource is deliberately NOT
    # patched. Production constructs the real class, so the real class's
    # constructor signature is what the tests exercise.

    for var in (
        "KAFKA_SECURITY_PROTOCOL",
        "KAFKA_SASL_MECHANISM",
        "KAFKA_SASL_USERNAME",
        "KAFKA_SASL_PASSWORD",
        "KAFKA_SSL_CA_LOCATION",
        "KAFKA_SSL_INSECURE",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def configure_existing(*topics: str) -> None:
    """Pretend these DLQ topics already exist on the broker."""
    FakeAdmin.list_topics_result = list(topics)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestRetentionAppliedToExistingTopics:
    @pytest.mark.asyncio
    async def test_configures_every_dlq_topic_and_counts_them(self):
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        count = await apply_dlq_retention("kafka:9092", "billing-service")
        admin = FakeAdmin.instances[0]

        assert count == len(dlqs)
        assert admin.started == 1
        assert len(admin.altered) == len(dlqs)
        assert admin.closed == 1
        assert [r.name for r in admin.altered] == dlqs

    @pytest.mark.asyncio
    async def test_applies_both_retention_and_segment_bounds(self):
        configure_existing(*sorted(all_dlq_topics()))
        await apply_dlq_retention("kafka:9092", "billing-service")
        for resource in FakeAdmin.instances[0].altered:
            assert resource.configs == {
                "retention.ms": str(DLQ_RETENTION_MS),
                "segment.ms": str(DLQ_SEGMENT_MS),
            }
            assert resource.resource_type == ConfigResourceType.TOPIC

    @pytest.mark.asyncio
    async def test_created_topics_are_not_also_altered(self):
        """A topic created in this call already carries the config via
        NewTopic, so it must be skipped in the alter loop."""
        configure_existing()  # nothing exists yet -> everything is missing
        dlqs = sorted(all_dlq_topics())
        count = await apply_dlq_retention("kafka:9092", "billing-service")
        admin = FakeAdmin.instances[0]

        assert count == len(dlqs)
        assert [t.name for t in admin.created] == dlqs
        assert admin.altered == []

    @pytest.mark.asyncio
    async def test_created_topic_carries_the_retention_config(self):
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing-service")
        for topic in FakeAdmin.instances[0].created:
            assert topic.num_partitions == 1
            assert topic.replication_factor == 1
            assert topic.topic_configs == {
                "retention.ms": str(DLQ_RETENTION_MS),
                "segment.ms": str(DLQ_SEGMENT_MS),
            }

    @pytest.mark.asyncio
    async def test_mixed_existing_and_missing(self):
        """Existing topics are altered; missing ones are created and counted once."""
        dlqs = sorted(all_dlq_topics())
        existing, missing = dlqs[:3], dlqs[3:]
        configure_existing(*existing)
        count = await apply_dlq_retention("kafka:9092", "billing-service")
        admin = FakeAdmin.instances[0]

        assert count == len(dlqs)
        assert [r.name for r in admin.altered] == existing
        assert [t.name for t in admin.created] == missing

    @pytest.mark.asyncio
    async def test_no_missing_topics_skips_create_entirely(self):
        configure_existing(*sorted(all_dlq_topics()))
        await apply_dlq_retention("kafka:9092", "billing-service")
        assert FakeAdmin.instances[0].created == []

    @pytest.mark.asyncio
    async def test_admin_is_always_closed(self):
        configure_existing(*sorted(all_dlq_topics()))
        await apply_dlq_retention("kafka:9092", "billing-service")
        assert FakeAdmin.instances[0].closed == 1

    @pytest.mark.asyncio
    async def test_uninspectable_new_topic_degrades_to_topic_config(self, monkeypatch):
        """The ``except (TypeError, ValueError)`` arm of the signature probe
        yields an empty param set, so ``topic_config`` is chosen and
        the modern ``FakeNewTopic`` rejects it — the resulting TypeError is
        swallowed by the outer handler (retention is best-effort)."""
        import inspect

        real_signature = inspect.signature

        def _boom(obj, *a, **kw):
            if obj is FakeNewTopic.__init__:
                raise TypeError("no signature available")
            return real_signature(obj, *a, **kw)

        monkeypatch.setattr(inspect, "signature", _boom)
        configure_existing()
        assert await apply_dlq_retention("kafka:9092", "billing") == 0
        assert FakeAdmin.instances[0].created == []


class TestLegacyTopicConfigSpelling:
    @pytest.mark.asyncio
    async def test_falls_back_to_topic_config_kwarg(self, monkeypatch):
        """A NewTopic whose signature has ``topic_config`` (older aiokafka)
        must be driven with that spelling — the :82 ternary."""
        import aiokafka.admin

        monkeypatch.setattr(aiokafka.admin, "NewTopic", LegacyNewTopic)
        configure_existing()
        count = await apply_dlq_retention("kafka:9092", "billing")
        admin = FakeAdmin.instances[0]
        assert count == len(all_dlq_topics())
        for topic in admin.created:
            assert topic.topic_config == {
                "retention.ms": str(DLQ_RETENTION_MS),
                "segment.ms": str(DLQ_SEGMENT_MS),
            }

    @pytest.mark.asyncio
    async def test_modern_spelling_is_preferred(self, monkeypatch):
        """With both spellings available the modern ``topic_configs`` wins."""
        import aiokafka.admin

        class BothSpellings(FakeNewTopic):
            def __init__(
                self,
                name: str,
                num_partitions: int = 1,
                replication_factor: int = 1,
                topic_configs: dict | None = None,
                topic_config: dict | None = None,
            ) -> None:
                super().__init__(name, num_partitions, replication_factor, topic_configs)
                self.topic_config = topic_config

        monkeypatch.setattr(aiokafka.admin, "NewTopic", BothSpellings)
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing")
        for topic in FakeAdmin.instances[0].created:
            assert topic.topic_configs["retention.ms"] == str(DLQ_RETENTION_MS)
            assert topic.topic_config is None


# ---------------------------------------------------------------------------
# Per-topic alter failure and the outer catch
# ---------------------------------------------------------------------------


class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_per_topic_alter_failure_is_best_effort(self, caplog):
        import logging

        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.alter_fail_for = {dlqs[0], dlqs[1]}
        with caplog.at_level(logging.WARNING, logger="wildframe_events.dlq_retention"):
            count = await apply_dlq_retention("kafka:9092", "billing")
        assert count == len(dlqs) - 2  # only the successes are counted
        assert len(FakeAdmin.instances[0].altered) == len(dlqs) - 2
        assert any("could not set retention on" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_a_per_topic_failure_carries_its_traceback(self, caplog):
        """An ACL denial and a network timeout both log "could not set
        retention"; without ``exc_info`` the two are indistinguishable, which
        is the failure-with-no-diagnostic this file has a history of."""
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.alter_fail_for = {dlqs[0]}
        with caplog.at_level(logging.WARNING, logger="wildframe_events.dlq_retention"):
            count = await apply_dlq_retention("kafka:9092", "billing")

        assert count == len(dlqs) - 1
        warnings = [r for r in caplog.records if "could not set retention" in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
        assert warnings[0].exc_info[0] is RuntimeError
        # The reason itself is recoverable from the log, not just the type.
        assert "alter_configs refused" in caplog.text

    @pytest.mark.asyncio
    async def test_start_failure_is_swallowed_and_still_closes(self, caplog):
        import logging

        FakeAdmin.start_error = RuntimeError("broker unreachable")
        with caplog.at_level(logging.ERROR, logger="wildframe_events.dlq_retention"):
            assert await apply_dlq_retention("kafka:9092", "billing") == 0
        assert FakeAdmin.instances[0].closed == 1
        assert any("DLQ retention enforcement skipped" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_list_topics_failure_is_swallowed(self):
        FakeAdmin.list_error = RuntimeError("metadata request failed")
        assert await apply_dlq_retention("kafka:9092", "billing") == 0
        assert FakeAdmin.instances[0].closed == 1

    @pytest.mark.asyncio
    async def test_create_topics_failure_is_swallowed(self):
        configure_existing()
        FakeAdmin.create_error = RuntimeError("TopicAlreadyExists")
        assert await apply_dlq_retention("kafka:9092", "billing") == 0
        assert FakeAdmin.instances[0].closed == 1

    @pytest.mark.asyncio
    async def test_never_raises_on_any_broker_error(self):
        FakeAdmin.start_error = RuntimeError("x")
        FakeAdmin.close_error = RuntimeError("y")
        assert await apply_dlq_retention("kafka:9092", "billing") == 0

    @pytest.mark.asyncio
    async def test_close_failure_is_swallowed(self):
        """The ``finally`` block must swallow a failing close()."""
        configure_existing(*sorted(all_dlq_topics()))
        FakeAdmin.close_error = RuntimeError("close timed out")
        assert await apply_dlq_retention("kafka:9092", "billing") == len(all_dlq_topics())
        assert FakeAdmin.instances[0].closed == 1

    @pytest.mark.asyncio
    async def test_returns_count_before_close_failure(self):
        configure_existing(*sorted(all_dlq_topics()))
        FakeAdmin.close_error = RuntimeError("close timed out")
        count = await apply_dlq_retention("kafka:9092", "billing")
        assert count == len(all_dlq_topics())


# ---------------------------------------------------------------------------
# alter_configs' return value: a non-raising call is not a successful one
# ---------------------------------------------------------------------------


class TestAlterConfigsResponseIsInspected:
    @pytest.mark.asyncio
    async def test_a_broker_error_code_is_not_counted_as_configured(self, caplog):
        """aiokafka 0.14.0 does not raise on a broker error code in the body,
        so the pre-fix ``configured += 1`` reported a topic as configured when
        the broker had actually refused it."""
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.alter_error_for = {
            dlqs[0]: (TOPIC_AUTHORIZATION_FAILED, "TOPIC_AUTHORIZATION_FAILED")
        }
        with caplog.at_level(logging.WARNING, logger="wildframe_events.dlq_retention"):
            count = await apply_dlq_retention("kafka:9092", "billing")

        assert count == len(dlqs) - 1, "a refused topic must not be counted"
        refusals = [r for r in caplog.records if "broker refused retention" in r.getMessage()]
        assert len(refusals) == 1
        assert dlqs[0] in refusals[0].getMessage()
        assert f"error_code={TOPIC_AUTHORIZATION_FAILED}" in refusals[0].getMessage()
        assert "TOPIC_AUTHORIZATION_FAILED" in refusals[0].getMessage()

    @pytest.mark.asyncio
    async def test_every_refused_topic_is_reported_and_uncounted(self, caplog):
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.alter_error_for = {
            topic: (TOPIC_AUTHORIZATION_FAILED, "TOPIC_AUTHORIZATION_FAILED") for topic in dlqs[:3]
        }
        with caplog.at_level(logging.WARNING, logger="wildframe_events.dlq_retention"):
            count = await apply_dlq_retention("kafka:9092", "billing")

        assert count == len(dlqs) - 3
        # Every topic was still attempted: retention enforcement is best-effort
        # per topic, and one ACL denial must not stop the rest.
        assert [r.name for r in FakeAdmin.instances[0].altered] == dlqs
        assert len([r for r in caplog.records if "broker refused retention" in r.getMessage()]) == 3

    @pytest.mark.asyncio
    async def test_a_successful_response_is_counted_and_silent(self, caplog):
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        with caplog.at_level(logging.WARNING, logger="wildframe_events.dlq_retention"):
            count = await apply_dlq_retention("kafka:9092", "billing")

        assert count == len(dlqs)
        assert not [r for r in caplog.records if "refused" in r.getMessage()]
        assert not [r for r in caplog.records if "could not set retention" in r.getMessage()]

    @pytest.mark.asyncio
    async def test_a_refusal_is_not_reported_as_an_exception(self, caplog):
        """The broker refused; nothing raised, so there is no traceback to log.
        The diagnostic is the code and message the broker sent."""
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.alter_error_for = {dlqs[0]: (TOPIC_AUTHORIZATION_FAILED, None)}
        with caplog.at_level(logging.WARNING, logger="wildframe_events.dlq_retention"):
            await apply_dlq_retention("kafka:9092", "billing")

        refusals = [r for r in caplog.records if "broker refused retention" in r.getMessage()]
        assert len(refusals) == 1
        assert refusals[0].exc_info is None
        assert "error_message=None" in refusals[0].getMessage()


class TestAlterConfigsRefusalParsing:
    """``_alter_configs_refusal`` against the real aiokafka response object."""

    def test_no_error_code_means_no_refusal(self):
        ok = AlterConfigsResponse_v0(
            throttle_time_ms=0,
            resources=[(0, None, _RESOURCE_TYPE_TOPIC, "billing.dlq")],
        )
        assert _alter_configs_refusal(ok) is None
        assert _alter_configs_refusal([ok]) is None  # real aiokafka returns a list

    def test_a_non_zero_code_is_reported_with_its_message(self):
        denied = AlterConfigsResponse_v0(
            throttle_time_ms=0,
            resources=[
                (TOPIC_AUTHORIZATION_FAILED, "TOPIC_AUTHORIZATION_FAILED", 2, "billing.dlq")
            ],
        )
        refusal = _alter_configs_refusal([denied])
        assert refusal is not None
        assert f"error_code={TOPIC_AUTHORIZATION_FAILED}" in refusal
        assert "TOPIC_AUTHORIZATION_FAILED" in refusal

    def test_the_first_refusal_wins(self):
        reply = AlterConfigsResponse_v0(
            throttle_time_ms=0,
            resources=[(0, None, 2, "ok.dlq"), (42, "InvalidRequest", 2, "bad.dlq")],
        )
        assert "error_code=42" in _alter_configs_refusal([reply])

    def test_an_error_code_without_a_message_is_still_caught(self):
        short = AlterConfigsResponse_v0(
            throttle_time_ms=0, resources=[(29, None, 2, "billing.dlq")]
        )
        assert "error_code=29" in _alter_configs_refusal(short)

    def test_a_missing_reply_is_not_a_refusal(self):
        assert _alter_configs_refusal(None) is None

    def test_a_reply_without_resources_is_not_a_refusal(self):
        class Bare:
            pass

        assert _alter_configs_refusal([Bare()]) is None

    def test_an_unexpected_resource_shape_is_ignored(self):
        class Odd:
            resources = [None, (), "not-a-tuple"]

        assert _alter_configs_refusal(Odd()) is None

    def test_a_bare_error_code_tuple_is_understood(self):
        class Odd:
            resources = [(29, "denied")]

        assert "error_code=29" in _alter_configs_refusal(Odd())


# ---------------------------------------------------------------------------
# AlterConfigs REPLACES a resource's whole config: describe, then merge
# ---------------------------------------------------------------------------


class TestDescribeThenMerge:
    """Retention is applied *on top of* each topic's existing config.

    ``AlterConfigs`` replaces the resource's entire config map, so sending
    only ``retention.ms``/``segment.ms`` would silently delete
    ``cleanup.policy`` and every other key the topic was configured with.
    """

    @pytest.mark.asyncio
    async def test_foreign_config_keys_survive_the_alter(self):
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.describe_result = {
            dlqs[0]: {
                "cleanup.policy": ("delete", False),
                "min.insync.replicas": ("2", False),
            }
        }

        assert await apply_dlq_retention("kafka:9092", "billing") == len(dlqs)

        assert FakeAdmin.instances[0].altered[0].configs == {
            "cleanup.policy": "delete",
            "min.insync.replicas": "2",
            "retention.ms": str(DLQ_RETENTION_MS),
            "segment.ms": str(DLQ_SEGMENT_MS),
        }

    @pytest.mark.asyncio
    async def test_the_new_bounds_win_over_the_described_ones(self):
        """An unbounded DLQ (-1) is the case this exists for."""
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.describe_result = {
            dlqs[0]: {"retention.ms": ("-1", False), "segment.ms": ("604800000", False)}
        }

        assert await apply_dlq_retention("kafka:9092", "billing") == len(dlqs)

        configs = FakeAdmin.instances[0].altered[0].configs
        assert configs["retention.ms"] == str(DLQ_RETENTION_MS)
        assert configs["segment.ms"] == str(DLQ_SEGMENT_MS)

    @pytest.mark.asyncio
    async def test_read_only_and_null_entries_are_not_echoed_back(self):
        """A describe response carries broker-owned keys too; re-sending a
        read-only key, or one the broker reports as null, is exactly what
        AlterConfigs rejects."""
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.describe_result = {
            dlqs[0]: {
                "cleanup.policy": ("compact", False),
                "segment.bytes": ("104857600", True),
                "local.retention.ms": (None, False),
            }
        }

        assert await apply_dlq_retention("kafka:9092", "billing") == len(dlqs)

        assert FakeAdmin.instances[0].altered[0].configs == {
            "cleanup.policy": "compact",
            "retention.ms": str(DLQ_RETENTION_MS),
            "segment.ms": str(DLQ_SEGMENT_MS),
        }

    @pytest.mark.asyncio
    async def test_a_topic_that_cannot_be_described_is_left_alone(self, caplog):
        """No description means no known config to merge onto, and altering
        blind is the data loss being avoided — so it is skipped, not counted."""
        dlqs = sorted(all_dlq_topics())
        configure_existing(*dlqs)
        FakeAdmin.describe_error_for = {dlqs[0]}

        with caplog.at_level(logging.WARNING, logger="wildframe_events.dlq_retention"):
            count = await apply_dlq_retention("kafka:9092", "billing")

        assert count == len(dlqs) - 1
        assert dlqs[0] not in [r.name for r in FakeAdmin.instances[0].altered]
        assert f"could not describe {dlqs[0]}" in caplog.text


# ---------------------------------------------------------------------------
# Admin connection kwargs / security branches
# ---------------------------------------------------------------------------


class TestAdminConnection:
    @pytest.mark.asyncio
    async def test_plaintext_admin_kwargs(self):
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing-service")
        kwargs = FakeAdmin.instances[0].kwargs
        assert kwargs["bootstrap_servers"] == "kafka:9092"
        assert kwargs["client_id"] == "billing-service-dlq-admin"
        assert kwargs["security_protocol"] == "PLAINTEXT"
        assert "ssl_context" not in kwargs
        assert "sasl_mechanism" not in kwargs

    @pytest.mark.asyncio
    async def test_sasl_kwargs_forwarded(self):
        configure_existing()
        await apply_dlq_retention(
            "kafka:9092",
            "billing",
            security_protocol="SASL_PLAINTEXT",
            sasl_mechanism="PLAIN",
            sasl_username="svc",
            sasl_password="pw",
        )
        kwargs = FakeAdmin.instances[0].kwargs
        assert kwargs["security_protocol"] == "SASL_PLAINTEXT"
        assert kwargs["sasl_mechanism"] == "PLAIN"
        assert kwargs["sasl_plain_username"] == "svc"
        assert kwargs["sasl_plain_password"] == "pw"

    @pytest.mark.asyncio
    async def test_security_settings_from_env(self, monkeypatch):
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "env-user")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "env-pw")
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing")
        kwargs = FakeAdmin.instances[0].kwargs
        assert kwargs["security_protocol"] == "SASL_SSL"
        assert kwargs["sasl_mechanism"] == "SCRAM-SHA-512"
        assert kwargs["sasl_plain_username"] == "env-user"
        assert kwargs["sasl_plain_password"] == "env-pw"

    @pytest.mark.asyncio
    async def test_explicit_ssl_context_forwarded(self):
        ctx = ssl.create_default_context()
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", ssl_context=ctx)
        assert FakeAdmin.instances[0].kwargs["ssl_context"] is ctx

    @pytest.mark.asyncio
    async def test_ca_env_builds_an_ssl_context(self, monkeypatch):
        seen: list[Any] = []
        monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/certs/ca.pem")
        monkeypatch.setattr(
            ssl,
            "create_default_context",
            lambda *, cafile=None: seen.append(cafile) or ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing")
        assert seen == ["/certs/ca.pem"]
        assert FakeAdmin.instances[0].kwargs["ssl_context"] is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("protocol", ["SSL", "SASL_SSL"])
    async def test_ssl_insecure_branch_disables_verification(self, monkeypatch, protocol):
        monkeypatch.setenv("KAFKA_SSL_INSECURE", "true")
        monkeypatch.setattr(
            ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        )
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", security_protocol=protocol)
        ctx = FakeAdmin.instances[0].kwargs["ssl_context"]
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["false", "0", "no", "NO"])
    async def test_ssl_verifying_when_insecure_disabled(self, monkeypatch, value):
        monkeypatch.setenv("KAFKA_SSL_INSECURE", value)
        monkeypatch.setattr(
            ssl, "create_default_context", lambda: pytest.fail("no context expected")
        )
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", security_protocol="SSL")
        assert "ssl_context" not in FakeAdmin.instances[0].kwargs

    @pytest.mark.asyncio
    async def test_plaintext_never_builds_an_ssl_context(self, monkeypatch):
        monkeypatch.setenv("KAFKA_SSL_INSECURE", "true")
        monkeypatch.setattr(
            ssl, "create_default_context", lambda: pytest.fail("no context expected")
        )
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", security_protocol="PLAINTEXT")
        assert "ssl_context" not in FakeAdmin.instances[0].kwargs


# ---------------------------------------------------------------------------
# The installed aiokafka surface the production code relies on
# ---------------------------------------------------------------------------


class TestInstalledAiokafkaApi:
    """The production code targets the real aiokafka API.

    These assertions are what the fakes above model; if a future aiokafka
    changes the surface, these fail and the fakes must follow.
    """

    def test_real_config_resource_takes_configs_in_the_constructor(self):
        # Captured at import time, before the autouse fixture swaps in the fake.
        import inspect

        params = inspect.signature(_REAL_CONFIG_RESOURCE.__init__).parameters
        assert "configs" in params
        assert not hasattr(_REAL_CONFIG_RESOURCE, "Type")
        assert not hasattr(_REAL_CONFIG_RESOURCE, "set_config")

    def test_real_list_topics_returns_a_plain_list(self):
        import inspect

        source = inspect.getsource(_REAL_ADMIN_CLIENT.list_topics)
        # Returns a list, so the code must not read `.topics` off it.
        assert "return [" in source
        assert not hasattr(["a", "b"], "topics")

    def test_real_new_topic_uses_topic_configs(self):
        import inspect

        params = inspect.signature(_REAL_NEW_TOPIC.__init__).parameters
        assert "topic_configs" in params
        assert "topic_config" not in params

    def test_real_admin_config_calls_take_a_list_of_resources(self):
        import inspect
        import typing

        for method in (
            _REAL_ADMIN_CLIENT.alter_configs,
            _REAL_ADMIN_CLIENT.describe_configs,
        ):
            params = [
                p for name, p in inspect.signature(method).parameters.items() if name != "self"
            ]
            assert typing.get_origin(params[0].annotation) is list, method.__name__

    def test_real_describe_response_exposes_the_keys_the_fake_models(self):
        """``_DescribeResponse.to_object()`` is hand-rolled; every key
        ``apply_dlq_retention`` reads off it must exist on the real reply."""
        from aiokafka.protocol.admin import DescribeConfigsResponse_v1

        response = DescribeConfigsResponse_v1(
            throttle_time_ms=0,
            resources=[
                (
                    0,
                    None,
                    ConfigResourceType.TOPIC,
                    "billing.dlq",
                    [("cleanup.policy", "delete", False, False, False, [])],
                )
            ],
        )
        resource = response.to_object()["resources"][0]
        assert resource["error_code"] == 0
        assert resource["resource_name"] == "billing.dlq"
        assert resource["config_entries"][0]["config_names"] == "cleanup.policy"
        assert resource["config_entries"][0]["config_value"] == "delete"
        assert resource["config_entries"][0]["read_only"] is False

    def test_real_aiokafka_producer_has_no_retries_parameter(self):
        """Cross-check for the publisher adapter's ``retries`` kwarg, which is
        unreachable for the same class of reason (a version-probing branch that
        never fires)."""
        import inspect

        assert "retries" not in inspect.signature(_REAL_PRODUCER.__init__).parameters
