"""Fail the run when a response was a sync iterator served under the ASGI handler.

Django drains such an iterator on a thread and warns once per response. After #129
every static file went out that way -- 126 warnings a run under a green summary,
which nobody reads. The fix is serving them async (``testproj/asgi.py``); this makes
the next regression fail instead of adding to a count.

Not ``filterwarnings = error``: raised inside the server, the warning breaks the
response after its headers, the browser never gets the script, and every E2E test
waits out its timeout -- a failure, but one that takes a quarter of an hour to
arrive. Counting it and failing the run at the end costs nothing.
"""

from __future__ import annotations

import warnings

import pytest

MESSAGE = "StreamingHttpResponse must consume synchronous iterators"

_seen: list[str] = []


def pytest_warning_recorded(warning_message: warnings.WarningMessage, when: str, nodeid: str, location) -> None:
    if MESSAGE in str(warning_message.message):
        _seen.append(nodeid or when)


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _seen and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter) -> None:
    if not _seen:
        return
    terminalreporter.write_sep("=", "sync iterators served under the ASGI handler", red=True)
    terminalreporter.write_line(
        f"{len(_seen)} response(s) warned {MESSAGE!r}. Serve them as async iterators "
        f"(static files: testproj/asgi.py). First in: {', '.join(sorted(set(_seen))[:5])}"
    )
