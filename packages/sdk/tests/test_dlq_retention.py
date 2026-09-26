"""Behavioural tests for ``apply_dlq_retention`` — bounded DLQ retention (#553).

Mocks are mandatory here, and the reason is a real API mismatch rather than
convenience. Against the installed **aiokafka 0.14.0**:

1. ``dlq_retention.py:106`` calls ``ConfigResource(ConfigResource.Type.TOPIC, t)``
   but ``aiokafka.admin.config_resource.ConfigResource`` has **no ``.Type``
   class attribute** — always ``AttributeError``.
2. ``dlq_retention.py:73`` reads ``(await admin.list_topics()).topics`` but
   ``AIOKafkaAdminClient.list_topics()`` returns ``list[str]`` — also always
   ``AttributeError``.

Both failures are swallowed by the outer ``except`` at :121, so lines 107-113
are unreachable without patching. The fakes below therefore reproduce what the
code *intended* to call, and two tests pin the real-library behaviour so the
mismatch cannot be silently forgotten.
"""

from __future__ import annotations

import ssl
from typing import Any

import pytest

from wildframe_events.dlq_retention import (
    DLQ_RETENTION_MS,
    DLQ_SEGMENT_MS,
    apply_dlq_retention,
)
from wildframe_events.topics import all_dlq_topics

#: The real aiokafka classes, captured BEFORE the autouse fixture below swaps
#: in fakes. ``TestRealAiokafkaApiMismatch`` needs the unpatched originals.
import aiokafka.admin as _real_admin  # noqa: E402
import aiokafka.admin.config_resource as _real_config_resource_module  # noqa: E402
import aiokafka as _real_aiokafka  # noqa: E402

_REAL_ADMIN_CLIENT = _real_admin.AIOKafkaAdminClient
_REAL_NEW_TOPIC = _real_admin.NewTopic
_REAL_CONFIG_RESOURCE = _real_config_resource_module.ConfigResource
_REAL_PRODUCER = _real_aiokafka.AIOKafkaProducer


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _TopicsResult:
    """What the code *expects* ``admin.list_topics()`` to return."""

    def __init__(self, topics: list[str]) -> None:
        self.topics = topics


class FakeAdmin:
    """Stand-in for ``aiokafka.admin.AIOKafkaAdminClient``."""

    instances: list["FakeAdmin"] = []

    #: Response for ``list_topics()``. Override per test.
    list_topics_result: Any = None
    #: ``alter_configs(resource)`` raises this, per topic name, when set.
    alter_fail_for: set[str] = set()
    #: ``start()`` raises this.
    start_error: Exception | None = None
    #: ``list_topics()`` raises this.
    list_error: Exception | None = None
    #: ``create_topics`` raises this.
    create_error: Exception | None = None
    #: ``close()`` raises this.
    close_error: Exception | None = None

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

    async def list_topics(self) -> Any:
        if type(self).list_error is not None:
            raise type(self).list_error
        result = type(self).list_topics_result
        return _TopicsResult(list(result)) if result is not None else _TopicsResult([])

    async def create_topics(self, new_topics: list[Any]) -> None:
        if type(self).create_error is not None:
            raise type(self).create_error
        self.created.extend(new_topics)

    async def alter_configs(self, resource: Any) -> None:
        if getattr(resource, "name", None) in type(self).alter_fail_for:
            raise RuntimeError(f"alter_configs refused for {resource.name}")
        self.altered.append(resource)

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

    ``dlq_retention.py:82`` probes the signature and picks whichever spelling
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


class FakeConfigResource:
    """Stand-in exposing the ``Type.TOPIC`` / ``set_config`` surface the code uses."""

    class Type:
        TOPIC = 2
        GROUP = 3

    def __init__(self, resource_type: int, name: str) -> None:
        self.resource_type = resource_type
        self.name = name
        self.configs: dict[str, str] = {}

    def set_config(self, key: str, value: str) -> None:
        self.configs[key] = value


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    import aiokafka.admin
    import aiokafka.admin.config_resource

    FakeAdmin.instances = []
    FakeAdmin.list_topics_result = None
    FakeAdmin.alter_fail_for = set()
    FakeAdmin.start_error = None
    FakeAdmin.list_error = None
    FakeAdmin.create_error = None
    FakeAdmin.close_error = None

    monkeypatch.setattr(aiokafka.admin, "AIOKafkaAdminClient", FakeAdmin)
    monkeypatch.setattr(aiokafka.admin, "NewTopic", FakeNewTopic)
    monkeypatch.setattr(aiokafka.admin.config_resource, "ConfigResource", FakeConfigResource)

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


def _declared_insecure_default(module) -> bool:
    """Read ``KAFKA_SSL_INSECURE``'s default straight out of the module source.

    The default has been flipped once already (``"true"`` -> ``"false"``, i.e.
    insecure-by-default -> verifying-by-default). Tests must not hard-code it:
    the contract is "the observed behaviour matches the declared default, and
    both branches are reachable via the env var", not a specific literal.
    """
    import ast
    import inspect

    source = inspect.getsource(module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "getenv" and node.args[0].value == "KAFKA_SSL_INSECURE":
            default = node.args[1].value
            return default.lower() not in ("false", "0", "no")
    raise AssertionError("KAFKA_SSL_INSECURE default not found in " + module.__name__)


def assert_default_matches_declaration(module, ssl_context) -> None:
    """The context the adapter built must agree with the declared default."""
    if _declared_insecure_default(module):
        assert ssl_context is not None
        assert ssl_context.check_hostname is False
        assert ssl_context.verify_mode == ssl.CERT_NONE
    else:
        assert ssl_context is None


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
            assert resource.resource_type == FakeConfigResource.Type.TOPIC

    @pytest.mark.asyncio
    async def test_created_topics_are_not_also_altered(self):
        """A topic created in this call already carries the config via
        NewTopic, so it must be skipped in the alter loop (the `continue` at
        :104-105)."""
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
        (:78-82) yields an empty param set, so ``topic_config`` is chosen and
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
            def __init__(self, name: str, num_partitions: int = 1, replication_factor: int = 1,
                         topic_configs: dict | None = None, topic_config: dict | None = None) -> None:
                super().__init__(name, num_partitions, replication_factor, topic_configs)
                self.topic_config = topic_config

        monkeypatch.setattr(aiokafka.admin, "NewTopic", BothSpellings)
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing")
        for topic in FakeAdmin.instances[0].created:
            assert topic.topic_configs["retention.ms"] == str(DLQ_RETENTION_MS)
            assert topic.topic_config is None


# ---------------------------------------------------------------------------
# Per-topic alter failure (:112-113) and the outer catch (:121-123)
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
        """The finally block at :124-128 must swallow a failing close()."""
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
            ssl, "create_default_context",
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
        monkeypatch.setattr(ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", security_protocol=protocol)
        ctx = FakeAdmin.instances[0].kwargs["ssl_context"]
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    @pytest.mark.asyncio
    @pytest.mark.parametrize("protocol", ["SSL", "SASL_SSL"])
    async def test_ssl_default_matches_the_declared_default(self, protocol):
        """Env var unset -> the behaviour the module declares, whichever that is."""
        import wildframe_events.dlq_retention as retention_mod

        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", security_protocol=protocol)
        ctx = FakeAdmin.instances[0].kwargs.get("ssl_context")
        assert_default_matches_declaration(retention_mod, ctx)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["false", "0", "no", "NO"])
    async def test_ssl_verifying_when_insecure_disabled(self, monkeypatch, value):
        monkeypatch.setenv("KAFKA_SSL_INSECURE", value)
        monkeypatch.setattr(ssl, "create_default_context", lambda: pytest.fail("no context expected"))
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", security_protocol="SSL")
        assert "ssl_context" not in FakeAdmin.instances[0].kwargs

    @pytest.mark.asyncio
    async def test_plaintext_never_builds_an_ssl_context(self, monkeypatch):
        monkeypatch.setenv("KAFKA_SSL_INSECURE", "true")
        monkeypatch.setattr(ssl, "create_default_context", lambda: pytest.fail("no context expected"))
        configure_existing()
        await apply_dlq_retention("kafka:9092", "billing", security_protocol="PLAINTEXT")
        assert "ssl_context" not in FakeAdmin.instances[0].kwargs


# ---------------------------------------------------------------------------
# Documented aiokafka 0.14.0 API mismatches (NOT fixed — reported, not patched)
# ---------------------------------------------------------------------------


class TestRealAiokafkaApiMismatch:
    """These two tests are the reason this file uses fakes at all.

    They run against the REAL installed aiokafka 0.14.0 classes and assert
    that the production code cannot work as written. If aiokafka ever ships
    the missing surface, these tests fail and the fakes can be dropped.
    """

    def test_real_config_resource_has_no_type_attribute(self):
        # Captured at import time, before the autouse fixture swaps in the fake.
        assert not hasattr(_REAL_CONFIG_RESOURCE, "Type"), (
            "aiokafka 0.14.0 ConfigResource has no .Type — "
            "dlq_retention.py:106 would now fail with AttributeError; "
            "the FakeConfigResource in this file can be removed"
        )

    def test_real_list_topics_returns_a_plain_list(self):
        import inspect

        source = inspect.getsource(_REAL_ADMIN_CLIENT.list_topics)
        assert "-> list[str]" in source, (
            "aiokafka 0.14.0 list_topics() returns list[str], so "
            "dlq_retention.py:73 `(await admin.list_topics()).topics` fails"
        )
        # And the value really has no `.topics` attribute to read.
        assert not hasattr(["a", "b"], "topics")

    def test_real_new_topic_uses_topic_configs(self):
        import inspect

        params = inspect.signature(_REAL_NEW_TOPIC.__init__).parameters
        assert "topic_configs" in params
        assert "topic_config" not in params

    def test_real_aiokafka_producer_has_no_retries_parameter(self):
        """Cross-check for publisher.py:209-217, which is unreachable for the
        same class of reason (a version-probing branch that never fires)."""
        import inspect

        assert "retries" not in inspect.signature(_REAL_PRODUCER.__init__).parameters
