"""Tests for engine - PolicyEngine stub."""
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import get_policy_for_jurisdiction
def test_engine_policy_eu():
    p = get_policy_for_jurisdiction(Jurisdiction.EU)
    assert p.jurisdiction == Jurisdiction.EU or p.jurisdiction == "EU"
def test_engine_policy_us():
    p = get_policy_for_jurisdiction(Jurisdiction.US)
    assert p is not None
