def fixture(func):
    """No‑op decorator for async fixtures used in tests.
    Returns the original function unchanged.
    """
    return func
