"""Regression tests for the Kafka bootstrap that issue #893 said was missing.

The defect: the broker runs ``AclAuthorizer`` with
``allow.everyone.if.no.acl.found=false`` and ``auto.create.topics.enable=false``,
and nothing in the repository ever created a topic or an ACL. Every service ran
with zero ACLs and every publish failed with ``TOPIC_AUTHORIZATION_FAILED``.

These tests deliberately assert on the *observable* side effects rather than on
the script's internals:

* ``TestComposeWiring`` reads deployments/docker-compose.dev.yml and checks that
  a bootstrap actually runs, that it runs after the broker is healthy, and that
  every Kafka-using service waits for it to have COMPLETED. A bootstrap nobody
  waits for is the ordering half of the bug.
* ``TestAclPlan`` reads the SDK's own ``_SERVICE_ACL`` matrix -- the module that
  documents itself as "the source of truth for Kafka ACLs / RBAC policy" -- and
  checks the plan grants exactly that and nothing else. In particular it fails
  on any wildcard principal or wildcard resource, because the tempting
  "fix" for a denial is to grant ``User:*``, which would turn this whole
  exercise back into #795.
* ``TestScriptExecution`` runs the script for real with stub ``kafka-topics`` /
  ``kafka-acls`` executables on PATH and inspects the argv they received. That
  is the layer the defect lives in: the ACL exists if and only if
  ``kafka-acls --add`` is invoked with the right triple. It also checks that a
  failing CLI makes the script exit non-zero, because a bootstrap that swallows
  broker errors would leave the stack in exactly the state this fixes.
* ``TestConsumerGroupDerivation`` checks the assumption that a service's
  consumer group is its own name, which is what licenses deriving the group ACL
  from the role name rather than hardcoding a second list.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/kafka_init.py"
COMPOSE = REPO / "deployments/docker-compose.dev.yml"
TOPICS_SRC = REPO / "packages/sdk/wildframe_events/topics.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_topics():
    # Loaded by path for the same reason scripts/kafka_init.py does it: the
    # wildframe_events package __init__ imports the publisher, which needs
    # aiokafka, and this test must run on a bare interpreter.
    return _load(TOPICS_SRC, "wildframe_topics_under_test")


def _load_init():
    assert SCRIPT.is_file(), (
        f"{SCRIPT.relative_to(REPO)} does not exist. Nothing creates Kafka topics "
        "or ACLs, so every publish is denied with TOPIC_AUTHORIZATION_FAILED "
        "(issue #893)."
    )
    return _load(SCRIPT, "kafka_init_under_test")


TOPICS = _load_topics()
INIT = _load_init()


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text())


@pytest.fixture(scope="module")
def services(compose):
    return compose["services"]


def env_of(block):
    """``environment:`` as a dict, whichever of the two compose forms is used.

    The file mixes ``KEY: value`` mappings and ``- KEY=value`` lists, and this
    suite reads it raw rather than through ``compose config`` so that a wiring
    mistake shows up as a test failure instead of being normalised away.
    """
    raw = block.get("environment") or {}
    if isinstance(raw, dict):
        return raw
    return dict(item.split("=", 1) for item in raw if "=" in item)


def mounts_of(block):
    """``volumes:`` as ``{container target: {source, read_only}}``."""
    mounts = {}
    for entry in block.get("volumes") or []:
        if isinstance(entry, dict):
            mounts[entry["target"]] = entry
            continue
        parts = entry.split(":")
        mounts[parts[1]] = {
            "source": parts[0],
            "read_only": "ro" in parts[2:],
        }
    return mounts


def depends_of(block):
    """``depends_on:`` as ``{name: condition}`` for both compose forms."""
    raw = block.get("depends_on") or {}
    if isinstance(raw, list):
        return {name: None for name in raw}
    return {name: (spec or {}).get("condition") for name, spec in raw.items()}


@pytest.fixture(scope="module")
def topic_plan():
    return INIT.build_topic_plan(TOPICS)


@pytest.fixture(scope="module")
def acl_plan():
    return INIT.build_acl_plan(TOPICS, {})


# ---------------------------------------------------------------------------
# Compose wiring
# ---------------------------------------------------------------------------


class TestComposeWiring:
    def test_a_bootstrap_service_exists(self, services):
        # Red against the pre-fix tree with this exact message: there is no
        # service that creates topics or ACLs, which *is* the defect.
        assert "kafka-init" in services, (
            "no kafka-init service in deployments/docker-compose.dev.yml: nothing "
            "creates the topics or the ACLs, so AclAuthorizer with "
            "allow.everyone.if.no.acl.found=false denies every publish (#893)"
        )

    def test_bootstrap_waits_for_a_healthy_broker(self, services):
        assert depends_of(services["kafka-init"]).get("kafka") == "service_healthy", (
            "kafka-init must wait for kafka to be healthy; kafka-topics and "
            "kafka-acls are clients and need a broker that is already listening"
        )

    def test_bootstrap_runs_the_repository_script(self, services):
        entrypoint = services["kafka-init"]["entrypoint"]
        assert entrypoint[0] == "python3"
        assert entrypoint[1] == "/opt/wildframe/kafka_init.py"
        mounts = mounts_of(services["kafka-init"])
        assert mounts["/opt/wildframe/kafka_init.py"]["read_only"] is True
        # Only topics.py, not the whole SDK: the package __init__ imports the
        # publisher, which needs aiokafka, which the cp-kafka image lacks.
        assert mounts["/opt/wildframe/topics.py"]["read_only"] is True

    def test_bootstrap_is_one_shot(self, services):
        # A restarted bootstrap would re-run the whole plan forever; kafka-acls
        # is idempotent but that is not a reason to leave it looping.
        assert services["kafka-init"]["restart"] == "no"

    def test_every_kafka_service_waits_for_the_bootstrap_to_complete(self, services):
        waiting, missing = [], []
        for name, block in services.items():
            if name == "kafka-init":
                continue  # it is the thing being waited on, not a waiter
            if "KAFKA_BOOTSTRAP_SERVERS" not in env_of(block):
                continue
            if depends_of(block).get("kafka-init") == "service_completed_successfully":
                waiting.append(name)
            else:
                missing.append(name)
        assert not missing, (
            "these services publish or consume but do not wait for kafka-init to "
            f"COMPLETE, so they race the ACLs: {sorted(missing)} (#893)"
        )
        assert len(waiting) == 15, f"expected all 15 Kafka services, got {len(waiting)}"

    def test_bootstrap_credentials_match_the_broker_block(self, services):
        """A username that drifts grants the ACL to a principal that cannot
        authenticate, and leaves the real principal denied -- a silent
        failure that looks exactly like the original bug."""
        pattern = re.compile(r"^[A-Z_]+_KAFKA_(?:USERNAME|PASSWORD)$")
        broker = {k for k in env_of(services["kafka"]) if pattern.match(k)}
        init = {k for k in env_of(services["kafka-init"]) if pattern.match(k)}
        assert init == broker, (
            "kafka-init and kafka disagree on the SASL credential variables; "
            f"only in kafka: {sorted(broker - init)}, only in kafka-init: "
            f"{sorted(init - broker)}"
        )

    def test_bootstrap_authenticates_as_the_super_user(self, services):
        env = env_of(services["kafka-init"])
        # Same variable deployments/kafka-entrypoint.sh reads for user_admin, so
        # one .env override moves the broker and the bootstrap together.
        assert env["WF_ADMIN_PASSWORD"] == env_of(services["kafka"])["WF_ADMIN_PASSWORD"]

    def test_authorization_is_not_disabled(self, services):
        """The symptom is a denial, and the tempting fix is to turn
        authorization off. That is what issue #795 asked us to stop doing."""
        env = env_of(services["kafka"])
        assert env["KAFKA_AUTHORIZER_CLASS_NAME"] == ("kafka.security.authorizer.AclAuthorizer")
        assert str(env["KAFKA_ALLOW_EVERYONE_IF_NO_ACL_FOUND"]).lower() == "false", (
            "ALLOW_EVERYONE_IF_NO_ACL_FOUND must stay false; setting it true "
            "disables authorization instead of granting the ACLs the architecture "
            "requires (#795)"
        )
        assert str(env["KAFKA_AUTO_CREATE_TOPICS_ENABLE"]).lower() == "false"


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


class TestAclPlan:
    def test_every_registered_topic_is_created(self, topic_plan):
        expected = sorted(set(TOPICS.all_topics()) | set(TOPICS.all_dlq_topics()))
        assert topic_plan == expected
        # A DLQ the SDK quarantines into but no broker holds loses events.
        assert len(topic_plan) == 2 * len(TOPICS.all_topics())

    def test_every_declared_acl_is_applied(self, acl_plan):
        topic_acls, group_acls = acl_plan
        applied = set(topic_acls)
        missing = []
        for role in TOPICS._SERVICE_ACL:
            principal = INIT.service_principal(role, {})
            acl = TOPICS.topic_acl(role, include_dlq=True)
            for topic in set(acl["produce"]):
                triple = INIT.Acl(principal, INIT.WRITE, INIT.TOPIC, topic)
                if triple not in applied:
                    missing.append(triple.render())
            for topic in set(acl["consume"]):
                triple = INIT.Acl(principal, INIT.READ, INIT.TOPIC, topic)
                if triple not in applied:
                    missing.append(triple.render())
        assert not missing, (
            "_SERVICE_ACL declares rights the bootstrap does not grant; those "
            f"services stay denied: {missing}"
        )

    def test_every_consumer_gets_exactly_one_group_grant(self, acl_plan):
        _, group_acls = acl_plan
        expected_roles = {
            role for role in TOPICS._SERVICE_ACL if TOPICS._SERVICE_ACL[role]["consume"]
        }
        assert {acl.principal for acl in group_acls} == set(expected_roles)
        # Per (principal, group), not per topic: four consumed topics is still
        # one JoinGroup authorisation.
        assert len(group_acls) == len(set(group_acls)) == len(expected_roles)

    def test_no_wildcard_principal_and_no_wildcard_resource(self, acl_plan):
        """A wildcard ACL is the failure mode this issue invites. Guard it."""
        topic_acls, group_acls = acl_plan
        for acl in list(topic_acls) + list(group_acls):
            assert acl.principal not in ("*", "User:*", "ANONYMOUS"), acl.render()
            assert "*" not in acl.resource, acl.render()
            assert acl.operation in (INIT.READ, INIT.WRITE), acl.render()

    def test_no_duplicate_grants(self, acl_plan):
        topic_acls, group_acls = acl_plan
        assert len(topic_acls) == len(set(topic_acls))
        assert len(group_acls) == len(set(group_acls))

    def test_principal_follows_the_environment_override(self):
        env = {"CONTENT_SERVICE_KAFKA_USERNAME": "content-svc-2"}
        topic_acls, _ = INIT.build_acl_plan(TOPICS, env)
        principals = {acl.principal for acl in topic_acls}
        assert "content-svc-2" in principals
        # Only the overridden service moves.
        assert "recommendation-service" in principals

    def test_default_principal_is_the_role_name(self):
        # The compose defaults are all role names, so an unset variable must not
        # invent a principal the broker has never heard of.
        assert INIT.service_principal("media-pipeline", {}) == "media-pipeline"
        assert INIT.service_principal("content-service", {}) == "content-service"

    def test_every_publisher_in_compose_has_a_produce_grant(self, services, acl_plan):
        """The runtime check that caught this bug: a service with
        EVENT_PUBLISHER=kafka whose principal gets no Write grant is denied on
        its very first event."""
        topic_acls, _ = acl_plan
        writers = {acl.principal for acl in topic_acls if acl.operation == INIT.WRITE}
        publishing = {
            name
            for name, block in services.items()
            if env_of(block).get("EVENT_PUBLISHER") == "kafka"
        }
        allowed = {INIT.service_principal(n, {}) for n in publishing}
        assert allowed <= writers, (
            "these services set EVENT_PUBLISHER=kafka but no produce ACL is "
            f"created for them: {sorted(allowed - writers)}"
        )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


STUB = """#!/bin/sh
# Records the exact argv this script invoked, so the test asserts on the command
# line rather than on the script's internals. One invocation per line, args
# separated by \\037 (unit separator), which appears in no topic name, principal
# or flag. NB `printf -- '---\\n'` is NOT a usable record separator: the format
# has no escape in it, so printf emits `---` with no trailing newline and the
# records run together.
{
    for arg in "$0" "$@"; do printf '%s\\037' "$arg"; done
    printf '\\n'
} >> "$KAFKA_STUB_LOG"
exit "${KAFKA_STUB_EXIT:-0}"
"""


@pytest.fixture
def stub_binaries(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "argv.log"
    for name in ("kafka-topics", "kafka-acls"):
        path = bindir / name
        path.write_text(STUB)
        path.chmod(0o755)
    return bindir, log


def _invocations(log: Path):
    if not log.exists():
        return []
    return [line.split("\037") for line in log.read_text().splitlines() if line.strip()]


def _run_script(stub_binaries, env_extra, tmp_path):
    bindir, log = stub_binaries
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["KAFKA_STUB_LOG"] = str(log)
    env["WF_ADMIN_PASSWORD"] = "test-only-not-a-real-secret"
    env.update(env_extra)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bootstrap",
            "kafka:29092",
            "--topics-registry",
            str(TOPICS_SRC),
            "--client-config",
            str(tmp_path / "client.properties"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, _invocations(log)


class TestScriptExecution:
    def test_creates_every_topic_then_adds_every_acl(self, stub_binaries, monkeypatch, tmp_path):
        completed, calls = _run_script(stub_binaries, {}, tmp_path)
        assert completed.returncode == 0, completed.stderr

        topics = [c for c in calls if c[0].endswith("kafka-topics")]
        acls = [c for c in calls if c[0].endswith("kafka-acls")]
        expected_topics = sorted(set(TOPICS.all_topics()) | set(TOPICS.all_dlq_topics()))
        created = [c[c.index("--topic") + 1] for c in topics]
        assert created == expected_topics
        assert all("--if-not-exists" in c for c in topics), "must be re-runnable"

        _, group_acls = INIT.build_acl_plan(TOPICS, {})
        topic_acls, group_acls = INIT.build_acl_plan(TOPICS, {})
        assert len(acls) == len(topic_acls) + len(group_acls)
        for command in acls:
            assert "--add" in command
            assert "--allow-principal" in command
            principal = command[command.index("--allow-principal") + 1]
            assert principal.startswith("User:")
            assert principal != "User:*"
            resource_flags = [f for f in ("--topic", "--group") if f in command]
            assert len(resource_flags) == 1, command
            value = command[command.index(resource_flags[0]) + 1]
            assert value != "*", command

    def test_group_grants_are_emitted_once_per_consumer(self, stub_binaries, monkeypatch, tmp_path):
        completed, calls = _run_script(stub_binaries, {}, tmp_path)
        assert completed.returncode == 0, completed.stderr
        groups = [c[c.index("--group") + 1] for c in calls if "--group" in c]
        expected = sorted(
            {
                INIT.consumer_group(role, {})
                for role in TOPICS._SERVICE_ACL
                if TOPICS._SERVICE_ACL[role]["consume"]
            }
        )
        assert sorted(groups) == expected
        assert len(groups) == len(set(groups))

    def test_client_properties_are_generated_not_committed(
        self, stub_binaries, monkeypatch, tmp_path
    ):
        config = tmp_path / "client.properties"
        completed, _ = _run_script(stub_binaries, {}, tmp_path)
        assert completed.returncode == 0, completed.stderr
        text = config.read_text()
        assert "security.protocol=SASL_SSL" in text
        assert "sasl.mechanism=PLAIN" in text
        assert "ssl.truststore.type=PEM" in text
        # Holds the admin password, so it must not be world/group readable.
        assert config.stat().st_mode & 0o077 == 0, oct(config.stat().st_mode)

    def test_missing_admin_password_fails_loudly(self, stub_binaries, monkeypatch, tmp_path):
        bindir, log = stub_binaries
        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env["KAFKA_STUB_LOG"] = str(log)
        env.pop("WF_ADMIN_PASSWORD", None)
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--topics-registry",
                str(TOPICS_SRC),
                "--client-config",
                str(tmp_path / "client.properties"),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 2
        assert "WF_ADMIN_PASSWORD" in completed.stderr
        # Nothing may have been attempted against the broker.
        assert _invocations(log) == []

    def test_a_failing_cli_is_not_swallowed(self, stub_binaries, tmp_path, monkeypatch):
        """A bootstrap that ignores broker errors leaves the stack in exactly
        the state this issue is about while reporting success."""
        # The script runs in a subprocess, so this cannot be tuned by patching
        # INIT: shorten the sleep through the environment the child inherits.
        monkeypatch.setenv("KAFKA_INIT_RETRY_DELAY_SECONDS", "0")
        completed, calls = _run_script(stub_binaries, {"KAFKA_STUB_EXIT": "1"}, tmp_path)
        assert completed.returncode == 1
        assert "failed" in completed.stderr.lower()
        # Bounded: one failing command per attempt, then it gives up rather than
        # looping forever. An unbounded retry here would hang `compose up`.
        assert len(calls) == INIT.MAX_ATTEMPTS

    def test_print_plan_needs_no_broker(self, monkeypatch, tmp_path):
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--topics-registry",
                str(TOPICS_SRC),
                "--print-plan",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={k: v for k, v in os.environ.items() if k != "PATH"},
        )
        assert completed.returncode == 0, completed.stderr
        assert "User:*" not in completed.stdout
        for topic in TOPICS.all_topics():
            assert topic in completed.stdout


# ---------------------------------------------------------------------------
# The consumer-group assumption
# ---------------------------------------------------------------------------


class TestConsumerGroupDerivation:
    """``consumer_group()`` defaults a role's group to its own name. That is a
    claim about the services, so it is checked against them rather than trusted.
    """

    GROUPS = {
        "auth-service": "services/auth-service/app/core/event_consumer.py",
        "search-service": "services/search-service/app/core/event_consumer.py",
        "user-service": "services/user-service/app/core/event_consumer.py",
    }

    def test_raw_consumers_use_their_own_name(self):
        for role, relative in self.GROUPS.items():
            source = (REPO / relative).read_text()
            declared = re.search(r'CONSUMER_GROUP\s*=\s*"([^"]+)"', source)
            assert declared, f"{relative} declares no CONSUMER_GROUP"
            assert declared.group(1) == role, (
                f"{relative} uses consumer group {declared.group(1)!r} but the "
                f"bootstrap derives {role!r}; the group ACL would not match"
            )

    def test_settings_backed_group_matches_too(self):
        source = (REPO / "services/recommendation-service/app/core/settings.py").read_text()
        declared = re.search(r'KAFKA_CONSUMER_GROUP:\s*str\s*=\s*"([^"]+)"', source)
        assert declared and declared.group(1) == "recommendation-service"

    def test_compose_group_ids_match(self, compose):
        for name, block in compose["services"].items():
            group = env_of(block).get("KAFKA_GROUP_ID")
            if group is not None:
                assert group == name, (
                    f"{name} has KAFKA_GROUP_ID={group!r}; the bootstrap derives "
                    "the group ACL from the service name"
                )
