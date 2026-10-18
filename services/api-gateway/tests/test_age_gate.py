"""Tests for age gate."""

import logging

from app.core.age_gate import JURISDICTION_MINOR_AGE, check_age_gate, is_age_restricted


class _Request:
    """The only thing `check_age_gate` reads off a request."""

    class _URL:
        def __init__(self, path):
            self.path = path

    def __init__(self, path):
        self.url = self._URL(path)


def test_age_restricted():
    assert is_age_restricted("/maturity/check") is True
    assert is_age_restricted("/health") is False


def test_minor_age_reaches_a_log_line_and_nothing_else(caplog):
    """KNOWN GAP (#1023) -- `JURISDICTION_MINOR_AGE` has exactly one consumer.

    The test this replaces asserted ``JURISDICTION_MINOR_AGE["EU"] == 16``: a
    constant equals itself, so it passed whatever the table said and proved
    nothing about behaviour.

    What is actually true, and worth pinning, is that the table is read in
    exactly one place -- the log line a minor access emits -- and that the
    value never reaches the decision. `caplog` is what makes that observable
    rather than asserted by faith; the matching half (no jurisdiction-based
    denial, the table value absent from the decision payload) lives in
    test_core_misc.py. If someone implements a real per-jurisdiction policy
    these two tests will go red, which is the point: that change is a legal
    decision and should not happen silently.
    """
    with caplog.at_level(logging.INFO, logger="app.core.age_gate"):
        decision = check_age_gate(
            _Request("/maturity/film"),
            x_age_verified="true",
            x_is_minor="true",
            x_jurisdiction="EU",
        )

    assert decision == {"age_verified": True, "is_minor": True, "jurisdiction": "EU"}
    # The logged number is the table's value, read from the table rather than
    # restated -- so a lookup that stopped consulting the table would go red.
    assert f"minor_age={JURISDICTION_MINOR_AGE['EU']}" in caplog.text
