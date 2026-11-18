"""Provisioned Grafana panel queries must match the label sets Prometheus has.

Issue #1134 found three query shapes that render a plausible but wrong number,
so a broken panel looks healthy instead of erroring:

* ``sum(up{...} == 0)``: without ``bool`` the comparison *filters* series, so
  the sum adds up the zeros it kept. "Services down" read 0 (or No data)
  during an outage. Count the survivors instead: ``count(... == 0) or vector(0)``.
* ``sum by (service) (process_*/python_gc_*)``: prometheus_client's process and
  GC collectors carry only the scrape labels ``job``/``instance``. Nothing
  (SDK or ``prometheus.yml``) adds ``service``, so every target collapses into
  one series with an empty legend.
* ``pg_database_connection_limit``: no database sets CONNECTION LIMIT, so it is
  -1 for all of them. The real ceiling is ``pg_settings_max_connections``.

Queries live in panel targets and inside the JSON-encoded ``left`` strings of
the Explore links, so both are scanned.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DASHBOARDS = REPO / "infrastructure" / "grafana" / "provisioning" / "dashboards"

_COMPARISON = re.compile(r"(==|!=|>=|<=|>|<)(?!\s*bool\b)")
_SUM_OPEN = re.compile(r"\bsum\s*(?:(?:by|without)\s*\([^)]*\)\s*)?\(")
_BY_SERVICE = re.compile(r"\bby\s*\([^)]*\bservice\b[^)]*\)")
_RUNTIME_METRIC = re.compile(r"\b(?:process_|python_gc_)\w+")


def _walk_exprs(node: object, where: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "expr" and isinstance(value, str):
                yield where, value
            elif key == "left" and isinstance(value, str):
                yield from _walk_exprs(json.loads(value), f"{where}.left")
            else:
                yield from _walk_exprs(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk_exprs(item, f"{where}[{i}]")


def _all_exprs() -> list[tuple[str, str]]:
    out = []
    for path in sorted(DASHBOARDS.glob("*.json")):
        out.extend(_walk_exprs(json.loads(path.read_text(encoding="utf-8")), path.name))
    return out


def _strip_matchers(expr: str) -> str:
    """Drop label matchers and quoted strings, whose =, != and =~ are not comparisons."""
    expr = re.sub(r'"(?:[^"\\]|\\.)*"', '""', expr)
    return re.sub(r"\{[^}]*\}", "", expr)


def _sum_bodies(expr: str) -> Iterator[str]:
    for match in _SUM_OPEN.finditer(expr):
        depth, start = 1, match.end()
        for i in range(start, len(expr)):
            depth += {"(": 1, ")": -1}.get(expr[i], 0)
            if depth == 0:
                yield expr[start:i]
                break


def _filtering_sum(expr: str) -> bool:
    return any(_COMPARISON.search(body) for body in _sum_bodies(_strip_matchers(expr)))


def _runtime_by_service(expr: str) -> bool:
    return bool(_RUNTIME_METRIC.search(expr) and _BY_SERVICE.search(expr))


EXPRS = _all_exprs()


def test_scanner_reaches_panels_and_explore_links() -> None:
    """Guard against a vacuous pass: both query locations must be scanned."""
    assert len(EXPRS) > 30, EXPRS
    assert any(".left" in where for where, _ in EXPRS), "Explore link queries not scanned"


@pytest.mark.parametrize(
    ("expr", "bad"),
    [
        ('sum(up{job="wildframe-services"} == 0)', True),
        ("sum by (job) (up < 1)", True),
        ('sum(up{job="wildframe-services"} == bool 0)', False),
        ('count(up{job="wildframe-services"} == 0) or vector(0)', False),
        ('sum(rate(http_requests_total{status_code=~"5.."}[5m]))', False),
        ('up{job="wildframe-services"} == 0', False),
    ],
)
def test_filtering_sum_detector(expr: str, bad: bool) -> None:
    assert _filtering_sum(expr) is bad


@pytest.mark.parametrize(
    ("expr", "bad"),
    [
        ("sum by (service) (process_open_fds)", True),
        ("sum(rate(python_gc_objects_collected_total[5m])) by (service)", True),
        ("sum by (instance) (process_open_fds)", False),
        ("sum by (service) (http_active_requests)", False),
    ],
)
def test_runtime_by_service_detector(expr: str, bad: bool) -> None:
    assert _runtime_by_service(expr) is bad


def test_no_sum_over_a_filtering_comparison() -> None:
    offenders = [(w, e) for w, e in EXPRS if _filtering_sum(e)]
    assert not offenders, (
        "sum() over a comparison without `bool` sums the surviving values, not "
        f"a count of them (use count(... == 0) or vector(0)): {offenders}"
    )


def test_runtime_metrics_not_grouped_by_missing_service_label() -> None:
    offenders = [(w, e) for w, e in EXPRS if _runtime_by_service(e)]
    assert not offenders, (
        "process_*/python_gc_* carry only job/instance; grouping by service "
        f"collapses every target into one unlabelled series: {offenders}"
    )


def test_no_per_database_connection_limit() -> None:
    offenders = [(w, e) for w, e in EXPRS if "pg_database_connection_limit" in e]
    assert not offenders, (
        "pg_database_connection_limit is -1 (no CONNECTION LIMIT set); use "
        f"pg_settings_max_connections: {offenders}"
    )
