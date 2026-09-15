"""Minimal pytest stub for admin-service tests.
Provides mark and raises used in repository tests.
"""

import contextlib


class _Mark:
    def __getattr__(self, name):
        def decorator(func):
            return func

        return decorator


mark = _Mark()


def raises(exc):
    @contextlib.contextmanager
    def _cm():
        try:
            yield
        except exc:
            return
        else:
            raise AssertionError(f"{exc} not raised")

    return _cm()
