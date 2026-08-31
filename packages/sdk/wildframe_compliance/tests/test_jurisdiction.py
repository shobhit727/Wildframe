"""Tests for jurisdiction enum."""

import pytest

from wildframe_compliance.jurisdiction import Jurisdiction


class TestJurisdiction:
    """Tests for Jurisdiction enum."""

    def test_eu_jurisdiction(self):
        assert Jurisdiction.EU.value == "EU"
        assert Jurisdiction.EU.display_name == "European Union"
        assert "GDPR" in Jurisdiction.EU.regulations
        assert "AVMS Directive" in Jurisdiction.EU.regulations
        assert Jurisdiction.EU.parent is None

    def test_us_jurisdictions(self):
        assert Jurisdiction.US.value == "US"
        assert Jurisdiction.US.display_name == "United States (Federal)"

        # Test state jurisdictions
        assert Jurisdiction.US_CA.value == "US-CA"
        assert Jurisdiction.US_CA.display_name == "California (CCPA/CPRA)"
        assert Jurisdiction.US_CA.parent == Jurisdiction.US
        assert "CCPA" in Jurisdiction.US_CA.regulations
        assert "CPRA" in Jurisdiction.US_CA.regulations

    def test_india_jurisdiction(self):
        assert Jurisdiction.IN.value == "IN"
        assert Jurisdiction.IN.display_name == "India"
        assert "DPDP Act" in Jurisdiction.IN.regulations
        assert "OTT Rules" in Jurisdiction.IN.regulations

    def test_global_jurisdiction(self):
        assert Jurisdiction.GLOBAL.value == "GLOBAL"
        assert Jurisdiction.GLOBAL.display_name == "Global Baseline"
        assert "ISO 27001" in Jurisdiction.GLOBAL.regulations

    def test_jurisdiction_parent_hierarchy(self):
        # State jurisdictions should have US as parent
        state_jurisdictions = [
            Jurisdiction.US_CA,
            Jurisdiction.US_VA,
            Jurisdiction.US_CO,
            Jurisdiction.US_CT,
            Jurisdiction.US_UT,
            Jurisdiction.US_TX,
            Jurisdiction.US_OR,
            Jurisdiction.US_MT,
            Jurisdiction.US_DE,
            Jurisdiction.US_NH,
            Jurisdiction.US_NJ,
            Jurisdiction.US_MN,
            Jurisdiction.US_MD,
            Jurisdiction.US_NE,
            Jurisdiction.US_RI,
            Jurisdiction.US_KY,
        ]
        for state in state_jurisdictions:
            assert state.parent == Jurisdiction.US

        # Quebec should have CA as parent
        assert Jurisdiction.CA_QC.parent == Jurisdiction.CA

        # EU and Global have no parent
        assert Jurisdiction.EU.parent is None
        assert Jurisdiction.GLOBAL.parent is None
        assert Jurisdiction.IN.parent is None

    def test_all_jurisdictions_have_display_names(self):
        for jurisdiction in Jurisdiction:
            assert jurisdiction.display_name
            assert len(jurisdiction.display_name) > 0

    def test_all_jurisdictions_have_regulations(self):
        for jurisdiction in Jurisdiction:
            assert jurisdiction.regulations
            assert len(jurisdiction.regulations) > 0
