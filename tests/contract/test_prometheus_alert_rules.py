"""The alerting pipeline is wired and internally consistent.

Issue #1117: the repo had zero Prometheus alert rules and no Alertmanager. A
rules file that never loads (wrong glob, missing mount) or a runbook anchor
that does not exist produces silent breakage, so this test pins the plumbing:

* ``prometheus.yml`` must declare ``rule_files`` and ``alerting``, or nothing
  is ever evaluated;
* every rule file must parse and carry severity, summary/description and a
  ``runbook_url`` whose anchor really exists in ``docs/OPERATIONS.md``;
* every scrape job must have an ``absent()`` alert, because ``up == 0``
  cannot fire for a job that has no targets at all;
* the Alertmanager config must not send anywhere: the owner has not approved
  notification destinations, so any Slack/PagerDuty/email integration here is
  a leak, not a feature.

The alert expressions themselves are proven to fire by the promtool unit test
at ``infrastructure/prometheus/tests/wildframe-alerts.test.yml``; this module
only checks repository invariants that promtool cannot see.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PROMETHEUS = REPO / "infrastructure" / "prometheus" / "prometheus.yml"
RULES_DIR = REPO / "infrastructure" / "prometheus" / "rules"
ALERTMANAGER = REPO / "infrastructure" / "alertmanager" / "alertmanager.yml"
OPERATIONS = REPO / "docs" / "OPERATIONS.md"

#: No destination is approved. Any receiver named here (or any of the usual
#: integration config keys) means alerts are leaving the box without approval.
FORBIDDEN_INTEGRATION_KEYS = (
    "slack_configs",
    "pagerduty_configs",
    "email_configs",
    "opsgenie_configs",
    "webhook_configs",
    "victorops_configs",
    "sns_configs",
    "telegram_configs",
    "discord_configs",
    "msteams_configs",
    "pushover_configs",
)

#: When a threshold comment is required: rules whose expr is a plain
#: threshold comparison must carry the approval-needed marker. The absent()
#: and up==0 alerts are not tunable proposals (absence is binary; a scrape
#: is up or down), so only the ratio/latency thresholds below are checked.
THRESHOLD_ALERTS = {"WildframeHighErrorRatio", "WildframeHighLatencyP95"}
PROPOSED_MARKER = "PROPOSED threshold — owner approval required"


def _prom_config() -> dict:
    return yaml.safe_load(PROMETHEUS.read_text(encoding="utf-8"))


def _rules_files() -> list[Path]:
    files = sorted(RULES_DIR.glob("*.yml"))
    assert files, f"no rules files in {RULES_DIR}: the rules dir is empty"
    return files


def _all_rules() -> list[dict]:
    out: list[dict] = []
    for path in _rules_files():
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        for group in parsed.get("groups", []):
            out.extend(group.get("rules", []))
    return out


def _anchor_ids(markdown: str) -> set[str]:
    """Explicit ``<a id="...">`` anchors plus GitHub-style heading slugs."""
    ids = set(re.findall(r'<a id="([^"]+)"></a>', markdown))
    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            slug = re.sub(r"[^\w\- ]", "", heading).lower().replace(" ", "-")
            ids.add(slug)
    return ids


def _runbook_anchors() -> set[str]:
    return _anchor_ids(OPERATIONS.read_text(encoding="utf-8"))


def test_prometheus_declares_rules_and_alerting() -> None:
    """Rules that never load and an alertmanager that is never contacted are
    both invisible failures, so both blocks are mandatory."""
    config = _prom_config()
    rule_files = config.get("rule_files") or []
    assert rule_files, "prometheus.yml declares no rule_files; alerts never load"
    assert any("rules" in str(glob) for glob in rule_files), (
        f"rule_files {rule_files} does not point at the rules directory"
    )
    alerting = config.get("alerting") or {}
    managers = alerting.get("alertmanagers") or []
    targets = [
        t
        for mgr in managers
        for sc in mgr.get("static_configs", [])
        for t in sc.get("targets", [])
    ]
    assert targets, "alerting.alertmanagers declares no targets"


def test_prometheus_alertmanager_target_matches_compose_service() -> None:
    """The alertmanager target must be the compose service name; a typo'd
    hostname sends alerts to a DNS NXDOMAIN forever."""
    config = _prom_config()
    targets = [
        t
        for mgr in (config.get("alerting") or {}).get("alertmanagers", [])
        for sc in mgr.get("static_configs", [])
        for t in sc.get("targets", [])
    ]
    compose = yaml.safe_load(
        (REPO / "deployments" / "docker-compose.dev.yml").read_text(encoding="utf-8")
    )
    services = set((compose.get("services") or {}).keys())
    assert targets, "no alertmanager targets configured"
    for target in targets:
        host = target.partition(":")[0]
        assert host in services, (
            f"alertmanager target {target!r} is not a compose service; "
            f"known services: {sorted(services)}"
        )


def test_rule_set_is_non_empty() -> None:
    rules = _all_rules()
    assert rules, "no alerting rules found in the rules directory"


def test_every_rule_has_severity_and_runbook_url() -> None:
    missing = []
    for rule in _all_rules():
        labels = rule.get("labels") or {}
        annotations = rule.get("annotations") or {}
        if "severity" not in labels:
            missing.append(f"{rule.get('alert')}: no severity label")
        for key in ("summary", "description", "runbook_url"):
            if not annotations.get(key):
                missing.append(f"{rule.get('alert')}: no {key} annotation")
    assert not missing, "rules are missing required labels/annotations: " + "; ".join(
        missing
    )


def test_every_runbook_url_points_at_a_real_anchor() -> None:
    anchors = _runbook_anchors()
    broken = []
    for rule in _all_rules():
        url = (rule.get("annotations") or {}).get("runbook_url") or ""
        fragment = url.partition("#")[2]
        if not fragment:
            broken.append(f"{rule.get('alert')}: runbook_url has no #anchor")
        elif fragment not in anchors:
            broken.append(
                f"{rule.get('alert')}: anchor #{fragment} does not exist in "
                f"docs/OPERATIONS.md"
            )
    assert not broken, "runbook anchors are broken: " + "; ".join(broken)


def test_threshold_rules_carry_proposed_marker() -> None:
    """Thresholds are proposals until the owner approves them; the marker in
    the rules file is the only record of that."""
    texts = {p.name: p.read_text(encoding="utf-8") for p in _rules_files()}
    missing = []
    for rule in _all_rules():
        if rule.get("alert") not in THRESHOLD_ALERTS:
            continue
        combined = "\n".join(texts.values())
        # The marker only counts if it sits in the block that defines the
        # alert; check the marker count is not zero overall, then that the
        # rule's expr block is preceded by it. Simplest robust check: the
        # marker appears at least once per threshold rule's yaml stanza.
        stanza = re.search(
            r"- alert: " + re.escape(str(rule["alert"])) + r"\n(.*?)(?=\n      - alert: |\n      - \w|\Z)",
            combined,
            re.DOTALL,
        )
        assert stanza, f"could not locate yaml stanza for {rule['alert']}"
        if PROPOSED_MARKER not in stanza.group(1):
            missing.append(rule["alert"])
    assert not missing, (
        f"threshold rules missing '{PROPOSED_MARKER}' comment: {missing}"
    )


def test_every_scrape_job_has_an_absent_alert() -> None:
    """``up == 0`` cannot fire for a job with no targets, so each job in
    prometheus.yml needs an absent() alert covering it."""
    jobs = [job["job_name"] for job in _prom_config()["scrape_configs"]]
    assert jobs, "prometheus.yml declares no scrape jobs; premise changed"
    absent_jobs: set[str] = set()
    for rule in _all_rules():
        expr = str(rule.get("expr", ""))
        if "absent(" not in expr:
            continue
        for job in jobs:
            if f'absent(up{{job="{job}"}})' in expr:
                absent_jobs.add(job)
    uncovered = set(jobs) - absent_jobs
    assert not uncovered, (
        f"scrape jobs without an absent() alert: {sorted(uncovered)}"
    )


def test_down_alerts_cover_the_scrape_jobs() -> None:
    """Each scrape job must also have an ``up == 0`` alert for the case the
    job exists but a target stops answering."""
    jobs = [job["job_name"] for job in _prom_config()["scrape_configs"]]
    down_jobs: set[str] = set()
    for rule in _all_rules():
        expr = str(rule.get("expr", ""))
        for job in jobs:
            if f'up{{job="{job}"}} == 0' in expr:
                down_jobs.add(job)
    uncovered = set(jobs) - down_jobs
    assert not uncovered, (
        f"scrape jobs without an up == 0 alert: {sorted(uncovered)}"
    )


def test_alertmanager_routes_only_to_null_receiver() -> None:
    """No approved destinations: everything must route to a receiver with no
    integration configs, or alerts leave the box without permission."""
    config = yaml.safe_load(ALERTMANAGER.read_text(encoding="utf-8"))

    receivers = config.get("receivers") or []
    assert receivers, "alertmanager.yml declares no receivers"
    leaky = []
    for receiver in receivers:
        integrations = [
            key for key in receiver if key in FORBIDDEN_INTEGRATION_KEYS
        ]
        if integrations:
            leaky.append(f"{receiver.get('name')}: {sorted(integrations)}")
    assert not leaky, (
        "the owner has not approved alert destinations; these receivers "
        f"have integration configs: {leaky}"
    )

    route = config.get("route") or {}
    assert route.get("receiver"), "default route has no receiver"
    assert route["receiver"] in {r.get("name") for r in receivers}, (
        f"default route {route.get('receiver')!r} names no declared receiver"
    )
    # A default route to a null receiver is the whole point while nothing is
    # approved; if a sub-route slips in pointing at a leaky receiver, the
    # receiver check above catches it, but a sub-route must also exist in the
    # receiver list.
    for sub in route.get("routes", []) or []:
        assert sub.get("receiver") in {r.get("name") for r in receivers}, (
            f"sub-route {sub} names no declared receiver"
        )
