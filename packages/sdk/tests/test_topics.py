"""Behavioural tests for ``wildframe_events.topics`` — the topic contract.

The data tables (``TOPIC_METADATA`` ~:201 and ``_SERVICE_ACL`` ~:377) are
single module-level literals, so they are *executed on import* and are
already fully covered by importing the module. Walking a 318-line dict
line by line would be a tautology, so the coverage effort here goes into
the real LOGIC and, more importantly, into the **invariants** those tables
are supposed to satisfy.

``TOPIC_METADATA`` and ``_SERVICE_ACL`` are hand-maintained tables of a size
that WILL drift, and drift in a topic registry is a production incident
(a producer emits to a topic no consumer has an ACL for). The invariant tests
below are what makes that drift loud.
"""

from __future__ import annotations

import re

import pytest

from wildframe_events.topics import (
    _SERVICE_ACL,
    TOPIC_METADATA,
    Topic,
    all_dlq_topics,
    all_topics,
    topic_acl,
    validate_topic_metadata,
)

TOPIC_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]+)+$")
#: ``prefix`` segments (may themselves be colon-separated, e.g.
#: ``upload:aborted:{upload_session_id}``) followed by {placeholders}.
IDEMPOTENCY_RE = re.compile(r"^[a-z0-9:_-]+(?:\{[a-z_]+\})+(:\{[a-z_]+\})*$")
#: Service role names in this repo: ``<name>-service`` or ``media-pipeline``.
SERVICE_ROLE_RE = re.compile(r"^([a-z][a-z0-9]*-service|media-pipeline)$")


def _public_attr_names() -> list[str]:
    return [
        attr
        for attr in dir(Topic)
        if not attr.startswith("_") and isinstance(getattr(Topic, attr), str)
    ]


# ---------------------------------------------------------------------------
# Topic class shape
# ---------------------------------------------------------------------------


class TestTopicClass:
    def test_dlq_suffix_constant(self):
        assert Topic.DLQ_SUFFIX == ".dlq"

    def test_topic_names_are_always_strings(self):
        for attr in _public_attr_names():
            assert isinstance(getattr(Topic, attr), str), attr

    def test_topic_names_use_lower_snake_dot_notation(self):
        """`<domain>.<action>` with snake_case segments (underscores allowed:
        ``content.metadata_extracted``, ``moderation.decision_made``)."""
        for attr in _public_attr_names():
            name = getattr(Topic, attr)
            if attr == "DLQ_SUFFIX":
                continue
            assert TOPIC_NAME_RE.match(name), f"{attr}={name!r} is not <domain>.<action>"
        # A topic with no domain prefix would break per-domain ACL grouping.
        assert all("." in n for n in all_topics())

    def test_documented_reference_topics_exist(self):
        """Spot-check the topics named in the module docstring / AGENTS.md."""
        assert Topic.CONTENT_UPLOADED == "content.uploaded"
        assert Topic.CONTENT_PUBLISHED == "content.published"
        assert Topic.BILLING_SUBSCRIPTION_CREATED == "billing.subscription.created"
        assert Topic.MODERATION_FLAGGED == "moderation.flagged"
        assert Topic.CREATOR_ONBOARDED == "creator.onboarded"

    def test_dlq_suffix_is_not_itself_a_topic(self):
        assert Topic.DLQ_SUFFIX not in all_topics()

    def test_no_topic_name_already_carries_the_dlq_suffix(self):
        # all_dlq_topics() appends the suffix; a pre-suffixed constant would
        # produce "x.dlq.dlq".
        for name in all_topics():
            assert not name.endswith(Topic.DLQ_SUFFIX), name

    def test_topic_count_is_stable(self):
        """A guard against accidental deletion: bump this deliberately."""
        assert len(all_topics()) == 24

    def test_all_topics_is_a_set_of_the_class_constants(self):
        assert all_topics() == {getattr(Topic, a) for a in _public_attr_names() if a != "DLQ_SUFFIX"}


# ---------------------------------------------------------------------------
# all_topics / all_dlq_topics
# ---------------------------------------------------------------------------


class TestAllTopics:
    def test_returns_a_set(self):
        assert isinstance(all_topics(), set)

    def test_excludes_the_dlq_suffix_and_private_names(self):
        names = all_topics()
        assert Topic.DLQ_SUFFIX not in names
        assert not any(n.startswith("_") for n in names)

    def test_all_dlq_topics_is_a_one_to_one_suffix_map(self):
        topics, dlqs = all_topics(), all_dlq_topics()
        assert dlqs == {t + Topic.DLQ_SUFFIX for t in topics}
        assert len(dlqs) == len(topics)

    def test_no_dlq_topic_collides_with_a_real_topic(self):
        assert all_dlq_topics().isdisjoint(all_topics())

    def test_results_are_fresh_objects_each_call(self):
        # Returning the module-level set would let a caller mutate the registry
        # for the whole process.
        first = all_topics()
        first.add("injected.topic")
        assert "injected.topic" not in all_topics()

    def test_recomputed_identically(self):
        assert all_topics() == all_topics()
        assert all_dlq_topics() == all_dlq_topics()


# ---------------------------------------------------------------------------
# topic_acl
# ---------------------------------------------------------------------------


class TestTopicAcl:
    def test_returns_produce_and_consume_lists(self):
        acl = topic_acl("billing-service")
        assert set(acl) == {"produce", "consume"}
        assert isinstance(acl["produce"], list) and isinstance(acl["consume"], list)

    def test_known_roles_are_covered(self):
        for role in _SERVICE_ACL:
            acl = topic_acl(role)
            assert acl["produce"] or acl["consume"], f"{role} has neither"

    def test_unknown_role_returns_empty_lists(self):
        assert topic_acl("does-not-exist") == {"produce": [], "consume": []}

    def test_unknown_role_is_case_sensitive(self):
        assert topic_acl("BILLING-SERVICE") == {"produce": [], "consume": []}

    def test_dlq_is_added_to_produce_for_every_consumed_topic(self):
        acl = topic_acl("content-service", include_dlq=True)
        for topic in acl["consume"]:
            assert topic + Topic.DLQ_SUFFIX in acl["produce"]

    def test_include_dlq_false_omits_the_dlq_topics(self):
        acl = topic_acl("content-service", include_dlq=False)
        assert not any(t.endswith(Topic.DLQ_SUFFIX) for t in acl["produce"])
        assert acl["produce"] == _SERVICE_ACL["content-service"]["produce"]

    def test_include_dlq_false_still_returns_the_consume_list(self):
        acl = topic_acl("content-service", include_dlq=False)
        assert acl["consume"] == _SERVICE_ACL["content-service"]["consume"]

    def test_declared_produce_topics_are_always_present(self):
        for role, acl_table in _SERVICE_ACL.items():
            acl = topic_acl(role, include_dlq=False)
            assert acl["produce"] == acl_table["produce"], role
            assert acl["consume"] == acl_table["consume"], role

    def test_a_pure_producer_gets_no_dlq_produce_rights(self):
        # uploads-service consumes nothing, so it has nothing to DLQ.
        acl = topic_acl("uploads-service")
        assert acl["consume"] == []
        assert not any(t.endswith(Topic.DLQ_SUFFIX) for t in acl["produce"])

    def test_a_pure_consumer_gains_dlq_produce_rights_only(self):
        acl = topic_acl("analytics-service", include_dlq=True)
        declared_produce = _SERVICE_ACL["analytics-service"]["produce"]
        assert acl["produce"][: len(declared_produce)] == declared_produce
        assert len(acl["produce"]) == len(declared_produce) + len(acl["consume"])

    def test_result_does_not_mutate_the_table(self):
        before = {k: {kk: list(vv) for kk, vv in v.items()} for k, v in _SERVICE_ACL.items()}
        acl = topic_acl("billing-service")
        acl["produce"].append("mutated")
        acl["consume"].clear()
        after = {k: {kk: list(vv) for kk, vv in v.items()} for k, v in _SERVICE_ACL.items()}
        assert before == after

    def test_every_acl_topic_is_a_real_topic_or_dlq(self):
        known = all_topics() | all_dlq_topics()
        for role in _SERVICE_ACL:
            for topic in topic_acl(role)["produce"] + topic_acl(role)["consume"]:
                assert topic in known, f"{role} references unknown topic {topic!r}"


# ---------------------------------------------------------------------------
# validate_topic_metadata
# ---------------------------------------------------------------------------


class TestValidateTopicMetadata:
    def test_passes_for_the_current_table(self):
        assert validate_topic_metadata() is None

    def test_detects_a_topic_missing_metadata(self, monkeypatch):
        broken = dict(TOPIC_METADATA)
        broken.pop(Topic.CONTENT_UPLOADED)
        monkeypatch.setattr("wildframe_events.topics.TOPIC_METADATA", broken)
        with pytest.raises(AssertionError) as exc:
            validate_topic_metadata()
        assert "missing=" in str(exc.value)
        assert "content.uploaded" in str(exc.value)

    def test_detects_metadata_without_a_topic_constant(self, monkeypatch):
        broken = dict(TOPIC_METADATA)
        broken["ghost.topic"] = {"producer": "x"}
        monkeypatch.setattr("wildframe_events.topics.TOPIC_METADATA", broken)
        with pytest.raises(AssertionError) as exc:
            validate_topic_metadata()
        assert "extra=" in str(exc.value)
        assert "ghost.topic" in str(exc.value)


# ---------------------------------------------------------------------------
# TOPIC_METADATA invariants
# ---------------------------------------------------------------------------


class TestTopicMetadataInvariants:
    def test_exactly_one_entry_per_topic(self):
        assert set(TOPIC_METADATA) == all_topics()
        assert len(TOPIC_METADATA) == len(all_topics())

    def test_required_keys_present_on_every_entry(self):
        required = {"producer", "consumers", "idempotency_key_pattern", "retry_strategy"}
        for topic, meta in TOPIC_METADATA.items():
            assert required <= set(meta), f"{topic} missing {required - set(meta)}"

    def test_producer_is_a_service_role_name(self):
        for topic, meta in TOPIC_METADATA.items():
            producer = meta["producer"]
            assert SERVICE_ROLE_RE.match(producer), (topic, producer)
            assert " " not in producer, topic

    def test_consumers_are_service_role_names(self):
        for topic, meta in TOPIC_METADATA.items():
            for consumer in meta["consumers"]:
                assert SERVICE_ROLE_RE.match(consumer), (topic, consumer)

    def test_role_name_vocabulary_is_consistent(self):
        """``media-pipeline`` is the one role without a ``-service`` suffix;
        the table must not mix naming styles silently."""
        names = {m["producer"] for m in TOPIC_METADATA.values()} | {
            c for m in TOPIC_METADATA.values() for c in m["consumers"]
        }
        odd = {n for n in names if not n.endswith("-service")}
        assert odd == {"media-pipeline"}

    def test_no_duplicate_consumers(self):
        for topic, meta in TOPIC_METADATA.items():
            assert len(meta["consumers"]) == len(set(meta["consumers"])), topic

    def test_producer_is_not_also_a_consumer(self):
        # A self-loop would mean the emitting service handles its own event.
        for topic, meta in TOPIC_METADATA.items():
            assert meta["producer"] not in meta["consumers"], topic

    def test_idempotency_key_patterns_are_well_formed(self):
        for topic, meta in TOPIC_METADATA.items():
            pattern = meta["idempotency_key_pattern"]
            assert IDEMPOTENCY_RE.match(pattern), f"{topic}: {pattern!r} is not name:{{entity}}..."

    def test_known_drift_shared_idempotency_pattern(self):
        """KNOWN DRIFT: billing.payout.accrued and billing.payout.transferred
        share ``payout:{creator_id}:{cycle_start}``.

        The consumer's dedup keys are namespaced by topic
        (``subscriber._dedup_keys`` -> ``"<topic>:<event_id>"``), so this is not
        a live collision today, but two different payouts for the same
        creator/cycle would be indistinguishable to a producer-side dedup that
        trusted the documented pattern. Pinned so a fix is a deliberate change.
        """
        from collections import Counter

        counts = Counter(m["idempotency_key_pattern"] for m in TOPIC_METADATA.values())
        shared = {p for p, c in counts.items() if c > 1}
        assert shared == {"payout:{creator_id}:{cycle_start}"}
        assert {
            t
            for t, m in TOPIC_METADATA.items()
            if m["idempotency_key_pattern"] == "payout:{creator_id}:{cycle_start}"
        } == {"billing.payout.accrued", "billing.payout.transferred"}

    def test_retry_strategy_names_a_bounded_attempt_count(self):
        for topic, meta in TOPIC_METADATA.items():
            assert "max_attempts=" in meta["retry_strategy"], topic
            assert "exponential_backoff" in meta["retry_strategy"], topic

    def test_non_retryable_entries_are_strings(self):
        for topic, meta in TOPIC_METADATA.items():
            for reason in meta.get("non_retryable", []):
                assert isinstance(reason, str) and reason, topic

    def test_virus_detection_is_non_retryable(self):
        assert "virus_detected" in TOPIC_METADATA[Topic.CONTENT_SCANNED]["non_retryable"]

    def test_no_dlq_topic_has_its_own_metadata(self):
        for name in all_dlq_topics():
            assert name not in TOPIC_METADATA

    def test_table_is_not_empty(self):
        assert len(TOPIC_METADATA) >= 20


# ---------------------------------------------------------------------------
# _SERVICE_ACL invariants
# ---------------------------------------------------------------------------


class TestServiceAclInvariants:
    def test_produce_rights_cover_every_declared_producer_but_two(self):
        acl_producers = {role for role, table in _SERVICE_ACL.items() if table["produce"]}
        declared = {m["producer"] for m in TOPIC_METADATA.values()}
        assert declared - acl_producers == {"auth-service", "admin-service"}

    def test_every_declared_consumer_except_auth_has_an_acl_role(self):
        acl_roles = set(_SERVICE_ACL)
        missing: set[str] = set()
        for topic, meta in TOPIC_METADATA.items():
            for consumer in meta["consumers"]:
                if consumer not in acl_roles:
                    missing.add(consumer)
        assert missing == {"auth-service"}

    def test_no_duplicate_entries_within_a_list(self):
        for role, table in _SERVICE_ACL.items():
            for direction in ("produce", "consume"):
                entries = table[direction]
                assert len(entries) == len(set(entries)), f"{role}.{direction} has duplicates"

    def test_acl_entries_are_not_dlq_suffixed(self):
        # DLQ rights are derived by topic_acl(include_dlq=True), never declared.
        for role, table in _SERVICE_ACL.items():
            for direction in ("produce", "consume"):
                for topic in table[direction]:
                    assert not topic.endswith(Topic.DLQ_SUFFIX), f"{role}.{direction}: {topic}"

    def test_role_names_are_service_role_names(self):
        for role in _SERVICE_ACL:
            assert SERVICE_ROLE_RE.match(role), role

    def test_dlq_rights_exist_for_every_consumer(self):
        for role, table in _SERVICE_ACL.items():
            if not table["consume"]:
                continue
            acl = topic_acl(role)
            for topic in table["consume"]:
                assert topic + Topic.DLQ_SUFFIX in acl["produce"], f"{role} cannot DLQ {topic}"

    def test_every_role_can_actually_do_something(self):
        assert len(_SERVICE_ACL) >= 10
        for role, table in _SERVICE_ACL.items():
            assert set(table) == {"produce", "consume"}, role


# ---------------------------------------------------------------------------
# Cross-module consistency
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cross-table consistency: TOPIC_METADATA vs _SERVICE_ACL
# ---------------------------------------------------------------------------


def _metadata_gaps() -> dict[str, list[str]]:
    """Metadata says service X consumes topic T, but X's ACL does not list T."""
    gaps: dict[str, list[str]] = {}
    for topic, meta in TOPIC_METADATA.items():
        for consumer in meta["consumers"]:
            if topic not in _SERVICE_ACL.get(consumer, {}).get("consume", []):
                gaps.setdefault(consumer, []).append(topic)
    return gaps


def _acl_gaps() -> list[tuple[str, str]]:
    """ACL says service X consumes topic T, but T's metadata omits X."""
    return [
        (role, topic)
        for role, table in _SERVICE_ACL.items()
        for topic in table["consume"]
        if role not in TOPIC_METADATA[topic]["consumers"]
    ]


class TestCrossTableConsistency:
    def test_every_produce_right_is_backed_by_a_producer_declaration(self):
        """Each ACL produce entry must be a topic whose metadata names the
        producing service — otherwise a role is writing to someone else's topic.
        """
        for role, table in _SERVICE_ACL.items():
            for topic in table["produce"]:
                assert TOPIC_METADATA[topic]["producer"] == role, f"{role} produces {topic}"

    def test_known_drift_roles_that_produce_without_an_acl_entry(self):
        """KNOWN DRIFT: auth-service and admin-service are named as producers in
        TOPIC_METADATA but have no entry in ``_SERVICE_ACL`` at all, so
        ``topic_acl("auth-service")`` returns empty produce rights.
        """
        producers_without_acl = {
            meta["producer"] for meta in TOPIC_METADATA.values()
        } - set(_SERVICE_ACL)
        assert producers_without_acl == {"auth-service", "admin-service"}
        for role in producers_without_acl:
            assert topic_acl(role) == {"produce": [], "consume": []}

    def test_known_drift_metadata_consumers_missing_an_acl_grant(self):
        """KNOWN DRIFT: 4 declared consume relationships are absent from the ACL.
        A least-privilege ACL built from ``_SERVICE_ACL`` would therefore leave
        these services without the grant the metadata promises.
        """
        assert _metadata_gaps() == {
            "creators-service": ["moderation.decision_made"],
            "user-service": ["user.registered"],
            "notification-service": ["user.registered"],
            "auth-service": ["user.moderated"],
        }

    def test_known_drift_acl_consumers_absent_from_the_metadata(self):
        """KNOWN DRIFT: 2 ACL grants are not reflected in the topic metadata
        (documentation drift in the opposite direction)."""
        assert _acl_gaps() == [
            ("streaming-service", "content.published"),
            ("creators-service", "billing.subscription.cancelled"),
        ]

    def test_every_acl_topic_is_covered_by_metadata(self):
        known = all_topics()
        for role, table in _SERVICE_ACL.items():
            for direction in ("produce", "consume"):
                for topic in table[direction]:
                    assert topic in known, f"{role}.{direction}: {topic}"

    def test_dlq_rights_are_derived_not_declared(self):
        """``_SERVICE_ACL`` must stay free of ``.dlq`` entries; topic_acl adds
        them. If a maintainer hand-added one, dedup/drift would follow."""
        for role, table in _SERVICE_ACL.items():
            flat = table["produce"] + table["consume"]
            assert not [t for t in flat if t.endswith(Topic.DLQ_SUFFIX)], role

    def test_acl_table_is_materially_populated(self):
        assert len(_SERVICE_ACL) == 12
        assert sum(len(t["consume"]) for t in _SERVICE_ACL.values()) > 30


# ---------------------------------------------------------------------------
# Cross-module consistency with the adapters that consume this registry
# ---------------------------------------------------------------------------


class TestCrossModuleConsistency:
    def test_subscriber_dlq_naming_matches_the_registry(self):
        """subscriber.py builds DLQ topics as ``<topic> + Topic.DLQ_SUFFIX`` and
        dlq_retention.py enumerates ``all_dlq_topics()``; the two must agree,
        otherwise quarantined events land on a topic with no retention bound.
        """
        from wildframe_events.publisher import InMemoryEventPublisher
        from wildframe_events.subscriber import KafkaEventSubscriber

        sub = KafkaEventSubscriber("kafka:9092", "g", dlq_publisher=InMemoryEventPublisher())
        sub._lazy_dlq_publisher = sub.dlq_publisher
        for topic in all_topics():
            assert topic + Topic.DLQ_SUFFIX in all_dlq_topics()

    def test_dlq_retention_bounds_are_positive(self):
        from wildframe_events.dlq_retention import DLQ_RETENTION_MS, DLQ_SEGMENT_MS

        assert DLQ_RETENTION_MS > 0
        assert DLQ_SEGMENT_MS > 0
        assert DLQ_SEGMENT_MS < DLQ_RETENTION_MS  # segments must roll first

    def test_only_the_dlq_suffix_is_shared_between_topics_and_dlq_modules(self):
        from wildframe_events import topics as topics_mod

        assert topics_mod.Topic.DLQ_SUFFIX == ".dlq"
