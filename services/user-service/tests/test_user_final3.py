def test_user_final3():
    from app.core.event_consumer import run_user_registered_consumer

    assert run_user_registered_consumer is not None
