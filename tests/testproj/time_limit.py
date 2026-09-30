"""Stop the run when one test takes too long, with every thread's stack (#148).

A test that hangs held the run until the CI job's own limit, and said nothing
about where. The render gate's mutation that always blocks did it twice: once in
the test body, and once more after the test had already failed, in
pytest-asyncio's teardown waiting on a task that never finished.

Not pytest-timeout, nor pytest's own ``faulthandler_timeout``: both stop their
timer as soon as a test fails (``pytest_exception_interact``), so the hang in the
teardown of a failed test -- the second one above -- still held the run. This
timer spans setup, call and teardown and nothing cancels it but the end of the
test, or a debugger. When it fires there is no way to carry on safely, so it
dumps the stacks and ends the process.

Ending the process skips every teardown after it. What that leaves behind:

- child processes a fixture started. One handed to ``own()`` is terminated
  first (the two uvicorn workers of ``test_multiworker_uploads.py``); any other
  is orphaned;
- the test database file, ``$TMPDIR/wireview-test-<pid>.sqlite3``;
- coverage data, under ``make test-cov``.

A debugger stops the timer: ``pytest_enter_pdb`` covers ``--pdb`` and
``breakpoint()``, and a test that starts under another debugger (one in
``sys.monitoring``'s debugger slot, as an IDE's or 3.14's pdb is, or one with a
trace function) gets none. ``-o test_time_limit=0`` turns it off for the run.

The timer is a thread, so it needs the GIL: a hang in C code that holds it is
not stopped.
"""

from __future__ import annotations

import faulthandler
import inspect
import os
import subprocess
import sys
import threading
import typing as t

import pytest

EXIT_CODE = 1

#: The modules a debugger's trace function comes from. Coverage traces too, and
#: must not turn the limit off.
DEBUGGERS = ("pydevd", "bdb", "pdb", "debugpy")

_timer: threading.Timer | None = None
_children: list[subprocess.Popen] = []


def own(proc: subprocess.Popen) -> subprocess.Popen:
    """Have a stop terminate ``proc``, which the fixture that started it no longer will."""
    _children.append(proc)
    return proc


def disown(proc: subprocess.Popen) -> None:
    if proc in _children:
        _children.remove(proc)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "test_time_limit",
        "Seconds one test may take, setup and teardown included, before the run is stopped. 0 turns it off.",
        default="60",
    )


def _debugging() -> bool:
    # Python 3.12+ debuggers (pydevd, debugpy, 3.14's pdb) attach through
    # sys.monitoring's debugger slot and leave no trace function. Coverage uses
    # its own slot, so it keeps the limit
    if sys.monitoring.get_tool(sys.monitoring.DEBUGGER_ID) is not None:
        return True
    trace = sys.gettrace()
    if trace is None:
        return False
    module = inspect.getmodule(trace) or inspect.getmodule(type(trace))
    return module is not None and any(part.startswith(DEBUGGERS) for part in module.__name__.split("."))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> t.Generator[None, object, object]:
    global _timer
    limit = float(item.config.getini("test_time_limit"))
    if limit <= 0 or _debugging():
        return (yield)
    _timer = threading.Timer(limit, _stop, (item, limit))
    _timer.daemon = True
    _timer.start()
    try:
        return (yield)
    finally:
        _cancel()


def pytest_enter_pdb(config: pytest.Config, pdb: object) -> None:
    # Whoever is at the prompt is not hung.
    _cancel()


def _cancel() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None


def _stop(item: pytest.Item, limit: float) -> None:
    try:
        # Captured output would be lost with the process; let the dump reach the terminal.
        capture = item.config.pluginmanager.getplugin("capturemanager")
        if capture is not None:
            capture.suspend_global_capture(in_=True)
        sys.stderr.write(f"\n\n{item.nodeid} took more than {limit:g}s (setup, call and teardown). Every thread:\n\n")
        sys.stderr.flush()
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        for proc in list(_children):
            if proc.poll() is None:
                proc.terminate()
        sys.stderr.write(
            "\nThe run stops here, without the teardowns after it: a child process not handed to"
            " testproj.time_limit.own() is left running, and the test database file is left behind."
            " To debug past the limit, run with -o test_time_limit=0.\n"
        )
        sys.stderr.flush()
    finally:
        # Whatever failed above, the run must not go on hanging.
        os._exit(EXIT_CODE)
