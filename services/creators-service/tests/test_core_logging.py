"""Behavioural tests for app/core/logging.py (creators-service).

The module is a 26-line logging helper that nothing in the app imports, so it is
exercised directly here. ``logging.basicConfig`` is a no-op once the root logger
already owns handlers, so the assertions target the observable outcome: the root
logger ends up at INFO and emitting records reaches a handler, plus the
``set_request_id`` / ``set_correlation_id`` context-var behaviour.
"""

import contextvars
import logging

import pytest

from app.core.logging import request_id_var, set_correlation_id, set_request_id, setup_logging

pytestmark = pytest.mark.unit


@pytest.fixture
def bare_root():
    """The root logger stripped of handlers, restored afterwards."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    root.setLevel(logging.NOTSET)
    try:
        yield root
    finally:
        for handler in root.handlers[:]:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _in_fresh_interpreter(script: str) -> str:
    """Run ``script`` in a clean interpreter and return its stdout.

    ``logging.basicConfig`` is a no-op when the root logger already owns
    handlers — which pytest guarantees for every test — so the "fresh process"
    behaviour of ``setup_logging`` has to be observed in its own interpreter.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ, PYTHONPATH=os.getcwd())
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _capture(root: logging.Logger) -> list[logging.LogRecord]:
    """Attach a collector to the root logger and return its record list."""
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    root.addHandler(Collector())
    return records


class TestSetupLogging:
    def test_installs_a_stream_handler_and_pins_info(self):
        """Exercised out-of-process: pytest always pre-installs root handlers,
        and ``logging.basicConfig`` then refuses to do anything."""
        result = _in_fresh_interpreter(
            "from app.core.logging import setup_logging;\n"
            "import logging;\n"
            "setup_logging()\n"
            "root = logging.getLogger()\n"
            "print('LEVEL', root.level)\n"
            "print('HANDLERS', [type(h).__name__ for h in root.handlers])"
        )

        assert "LEVEL 20" in result
        assert "StreamHandler" in result

    def test_info_and_above_are_emitted_but_debug_is_filtered(self):
        result = _in_fresh_interpreter(
            "from app.core.logging import setup_logging;\n"
            "import logging\n"
            "setup_logging()\n"
            "root = logging.getLogger()\n"
            "seen = []\n"
            "class C(logging.Handler):\n"
            "    def emit(self, record): seen.append(record.getMessage())\n"
            "root.addHandler(C())\n"
            "log = logging.getLogger('app.probe')\n"
            "log.info('hello')\n"
            "log.debug('noisy')\n"
            "print('SEEN', seen)"
        )

        assert "SEEN ['hello']" in result

    def test_is_a_noop_when_the_root_already_has_a_handler(self, bare_root):
        """logging.basicConfig does nothing once root owns handlers."""
        setup_logging()
        handler_count = len(bare_root.handlers)
        bare_root.setLevel(logging.NOTSET)

        setup_logging()

        assert len(bare_root.handlers) == handler_count
        assert bare_root.level == logging.NOTSET

    def test_can_be_called_repeatedly_without_raising(self, bare_root):
        setup_logging()
        setup_logging()
        records = _capture(bare_root)

        logging.getLogger("app.probe").info("still works")

        assert "still works" in [r.getMessage() for r in records]


class TestRequestId:
    def test_returns_and_stores_a_uuid4(self):
        first = set_request_id()
        second = set_request_id()

        assert first != second
        assert len(first) == 36
        assert first.count("-") == 4
        assert request_id_var.get() == second

    def test_default_is_an_empty_string(self):
        # A fresh context starts from the declared default, not the last value.
        assert contextvars.Context().run(request_id_var.get) == ""


class TestCorrelationId:
    def test_returns_the_value_it_is_given(self):
        assert set_correlation_id("cid-1") == "cid-1"
        assert set_correlation_id("cid-2") == "cid-2"

    def test_does_not_mutate_the_request_id_context(self):
        rid = set_request_id()

        set_correlation_id("cid-1")

        assert request_id_var.get() == rid
