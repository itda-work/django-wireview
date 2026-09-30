"""Stop the run when one test takes too long, with every thread's stack (#148).

A test that hangs held the run until the CI job's own limit, and said nothing
about where. The render gate's mutation that always blocks did it twice: once in
the test body, and once more after the test had already failed, in
pytest-asyncio's teardown waiting on a task that never finished.

Not pytest-timeout, nor pytest's own ``faulthandler_timeout``: both stop their
timer as soon as a test fails (``pytest_exception_interact``), so the hang in the
teardown of a failed test -- the second one above -- still held the run. This
timer spans setup, call and teardown and nothing cancels it but the end of the
test. When it fires there is no way to carry on safely, so it dumps the stacks
and ends the process.

``-o test_time_limit=0`` turns it off, for a debugger session.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import typing as t

import pytest

EXIT_CODE = 1


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "test_time_limit",
        "Seconds one test may take, setup and teardown included, before the run is stopped. 0 turns it off.",
        default="60",
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> t.Generator[None, object, object]:
    limit = float(item.config.getini("test_time_limit"))
    if limit <= 0:
        return (yield)
    timer = threading.Timer(limit, _stop, (item, limit))
    timer.daemon = True
    timer.start()
    try:
        return (yield)
    finally:
        timer.cancel()


def _stop(item: pytest.Item, limit: float) -> None:
    # Captured output would be lost with the process; let the dump reach the terminal.
    capture = item.config.pluginmanager.getplugin("capturemanager")
    if capture is not None:
        capture.suspend_global_capture(in_=True)
    sys.stderr.write(f"\n\n{item.nodeid} took more than {limit:g}s (setup, call and teardown). Every thread:\n\n")
    sys.stderr.flush()
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    sys.stderr.flush()
    os._exit(EXIT_CODE)
