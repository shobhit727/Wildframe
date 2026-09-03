"""SDK 80%."""
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import get_policy_for_jurisdiction
def test_all_jurisdictions():
    for j in [Jurisdiction.EU, Jurisdiction.US, Jurisdiction.IN, Jurisdiction.GLOBAL]:
        p = get_policy_for_jurisdiction(j)
        assert p.jurisdiction == j or p.jurisdiction.value == j.value
def test_policy_consent_age():
    from wildframe_compliance.policy import GDPRPolicy, USPrivacyPolicy, IndiaDPDPPolicy
    assert GDPRPolicy().consent_minor_age == 16
    assert USPrivacyPolicy().consent_minor_age == 13
    assert IndiaDPDPPolicy().consent_minor_age == 18
