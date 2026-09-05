def test_creators_final2_a():
    from app.models import CreatorCommerce

    assert CreatorCommerce is not None


def test_creators_final2_b():
    from app.schemas.payout import PayoutCreate

    assert PayoutCreate is not None
