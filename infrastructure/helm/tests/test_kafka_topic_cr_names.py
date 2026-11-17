#!/usr/bin/env python3
"""Chart policy test: KafkaTopic CR names must be valid Kubernetes names (#1128).

``metadata.name`` of a custom resource must be a lowercase RFC 1123 subdomain;
the API server rejects anything else, so ``content.metadata_extracted`` and
``moderation.decision_made`` could never be created and `helm lint` warned on
them. The real Kafka topic is carried in ``spec.topicName`` (Strimzi uses it
instead of ``metadata.name`` when set), so the topic itself must not change.

Dots are valid in a subdomain, so already-valid CR names must stay exactly as
they are: deleting a managed KafkaTopic makes Strimzi's Topic Operator delete
the topic in Kafka, so renaming a live CR on `helm upgrade` would drop its data.
"""

import ast
import re
import subprocess
from pathlib import Path

import yaml

HELM = Path(__file__).resolve().parent.parent
CHART = HELM / "wildframe"
TOPICS_PY = HELM.parent.parent / "packages" / "sdk" / "wildframe_events" / "topics.py"

RENDERS = [
    ("wildframe", None),
    ("wildframe-staging", HELM / "values-staging.yaml"),
    ("wildframe-production", HELM / "values-production.yaml"),
]

# Kubernetes' DNS-1123 subdomain (k8s.io/apimachinery validation.IsDNS1123Subdomain).
RFC1123_SUBDOMAIN = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")


def canonical_topics():
    tree = ast.parse(TOPICS_PY.read_text())
    (topic_cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Topic"]
    topics = set()
    for node in topic_cls.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            (target,) = node.targets
            if target.id != "DLQ_SUFFIX":
                topics.add(node.value.value)
    assert len(topics) == 24, sorted(topics)
    return topics


def chart_args(namespace, values_file):
    args = [str(CHART), "--namespace", namespace]
    if values_file:
        args += ["-f", str(values_file)]
    return args


def kafka_topics(namespace, values_file):
    res = subprocess.run(
        ["helm", "template", "wildframe", *chart_args(namespace, values_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"helm template {values_file} failed: {res.stderr}"
    docs = [d for d in yaml.safe_load_all(res.stdout) if d and d["kind"] == "KafkaTopic"]
    assert docs, f"{values_file}: no KafkaTopic rendered"
    return docs


def test_kafka_topic_names_are_rfc1123_and_unique():
    for namespace, values in RENDERS:
        names = [d["metadata"]["name"] for d in kafka_topics(namespace, values)]
        bad = [n for n in names if len(n) > 253 or not RFC1123_SUBDOMAIN.fullmatch(n)]
        assert not bad, f"{values}: KafkaTopic names are not RFC 1123 subdomains: {bad}"
        assert len(names) == len(set(names)), f"{values}: KafkaTopic name collision: {names}"


def test_topic_name_is_the_canonical_kafka_topic():
    canonical = canonical_topics()
    for namespace, values in RENDERS:
        docs = kafka_topics(namespace, values)
        topic_names = [d["spec"].get("topicName") for d in docs]
        assert all(topic_names), f"{values}: every KafkaTopic must set spec.topicName"
        assert len(topic_names) == len(set(topic_names)), f"{values}: duplicate topicName"
        assert set(topic_names) == canonical, (
            f"{values}: topicName drifted from wildframe_events.topics: "
            f"missing={canonical - set(topic_names)} extra={set(topic_names) - canonical}"
        )


def test_already_valid_cr_names_are_not_renamed():
    for namespace, values in RENDERS:
        for doc in kafka_topics(namespace, values):
            # Strimzi falls back to metadata.name when spec.topicName is unset.
            topic = doc["spec"].get("topicName", doc["metadata"]["name"])
            if RFC1123_SUBDOMAIN.fullmatch(topic):
                assert doc["metadata"]["name"] == topic, (
                    f"{values}: KafkaTopic for {topic} renamed to {doc['metadata']['name']}; "
                    "a renamed CR is deleted on upgrade and Strimzi deletes its topic"
                )


def test_helm_lint_reports_no_naming_warnings():
    for namespace, values in RENDERS:
        res = subprocess.run(
            ["helm", "lint", *chart_args(namespace, values)], capture_output=True, text=True
        )
        assert res.returncode == 0, f"helm lint {values} failed: {res.stdout}{res.stderr}"
        warnings = [line for line in res.stdout.splitlines() if "naming requirements" in line]
        assert not warnings, f"helm lint {values}: {warnings}"
