"""Test configuration for wildframe-compliance."""

import pytest


@pytest.fixture
def eu_settings():
    """Settings configured for EU jurisdiction."""
    from wildframe_compliance.settings import ComplianceSettingsMixin
    from wildframe_compliance.jurisdiction import Jurisdiction

    class EUSettings(ComplianceSettingsMixin):
        SERVICE_NAME: str = "test-service"
        compliance_jurisdiction: Jurisdiction = Jurisdiction.EU

    return EUSettings()


@pytest.fixture
def us_settings():
    """Settings configured for US jurisdiction."""
    from wildframe_compliance.settings import ComplianceSettingsMixin
    from wildframe_compliance.jurisdiction import Jurisdiction

    class USSsettings(ComplianceSettingsMixin):
        SERVICE_NAME: str = "test-service"
        compliance_jurisdiction: Jurisdiction = Jurisdiction.US

    return USSsettings()


@pytest.fixture
def india_settings():
    """Settings configured for India jurisdiction."""
    from wildframe_compliance.settings import ComplianceSettingsMixin
    from wildframe_compliance.jurisdiction import Jurisdiction

    class IndiaSettings(ComplianceSettingsMixin):
        SERVICE_NAME: str = "test-service"
        compliance_jurisdiction: Jurisdiction = Jurisdiction.IN

    return IndiaSettings()
