"""Minimal pytest stub for test imports.
Provides only the symbols used in admin-service repository tests.
"""
import contextlib

class _Mark:
    def __getattr__(self, name):
        # Return a decorator that leaves the function unchanged.
        def decorator(func):
            return func
        return decorator

mark = _Mark()

def raises(expected_exception):
    """Context manager mimicking ``pytest.raises``.
    Usage: ``with pytest.raises(SomeError):``
    """
    @contextlib.contextmanager
    def _cm():
        try:
            yield
        except expected_exception:
            return
        else:
            raise AssertionError(f"{expected_exception} not raised")
    return _cm()
