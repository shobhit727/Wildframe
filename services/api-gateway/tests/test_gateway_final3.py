"""The modules that supply the gateway's routing surface are actually loaded."""


def test_gateway_final3():
    """Each named module contributes a live route or a registry the proxy uses."""
    from app.api.gateway_routes import list_services, proxy_request
    from app.core.age_gate import AGE_RESTRICTED_PREFIXES
    from app.core.billing_proxy import BILLING_RATE_LIMITS

    # The proxy entrypoints are coroutines the router actually awaits.
    import inspect

    assert inspect.iscoroutinefunction(proxy_request)
    assert inspect.iscoroutinefunction(list_services)

    # The policy tables the edge consults are populated, not empty stubs.
    assert AGE_RESTRICTED_PREFIXES
    assert BILLING_RATE_LIMITS["GLOBAL"] > 0
