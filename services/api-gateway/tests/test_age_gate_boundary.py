"""Path-segment boundary matching for the age gate (#879).

`is_age_restricted` matched with ``str.startswith`` and no segment boundary, so
any path that merely shared a leading substring with a restricted prefix was
treated as age-restricted and 403'd: ``/maturityx`` and ``/purchase-order/list``
both matched. The direction is fail-safe (over-blocks rather than under-blocks),
but it means an unrelated sibling route is refused as soon as a prefix gains a
neighbour, and the over-match is indistinguishable from a real gate hit.

These tests pin the boundary rule and, more importantly, prove it still matches
on the **prefix itself** and on everything beneath it -- a boundary check that
forgot ``path == prefix`` would silently un-gate the restricted routes.
"""

import pytest

from app.core.age_gate import AGE_RESTRICTED_PREFIXES, is_age_restricted

# Sibling routes that share a leading substring with a restricted prefix but
# are NOT under it. Each is the exact shape the raw-prefix match got wrong.
SIBLING_PATHS = [
    "/maturityx",
    "/maturity-chart",
    "/purchases",
    "/purchase-order/list",
    "/content/restrictedxyz",
    "/content/restricted-archive/1",
]


@pytest.mark.parametrize("path", sorted(AGE_RESTRICTED_PREFIXES))
def test_the_prefix_itself_is_still_restricted(path):
    """A boundary check that only looked at descendants would un-gate these."""
    assert is_age_restricted(path) is True


@pytest.mark.parametrize("path", sorted(AGE_RESTRICTED_PREFIXES))
def test_every_descendant_of_a_prefix_is_still_restricted(path):
    assert is_age_restricted(f"{path}/detail") is True
    assert is_age_restricted(f"{path}/") is True
    assert is_age_restricted(f"{path}/a/b/c") is True


@pytest.mark.parametrize("path", SIBLING_PATHS)
def test_a_sibling_route_sharing_only_a_prefix_is_not_restricted(path):
    """The defect: raw startswith gated these purely on a shared substring."""
    assert is_age_restricted(path) is False


@pytest.mark.parametrize(
    "path",
    ["/content/catalog", "/health", "/", "/api/v1/titles", "/streaming/play"],
)
def test_unrelated_routes_are_not_restricted(path):
    assert is_age_restricted(path) is False
