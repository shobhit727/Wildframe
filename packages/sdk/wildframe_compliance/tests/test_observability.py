"""Tests for observability."""
def test_observability_import():
    import wildframe_compliance
    assert wildframe_compliance is not None
def test_jurisdiction():
    from wildframe_compliance.jurisdiction import Jurisdiction
    assert Jurisdiction.EU.value == "EU"
