"""The E2E harness has a contract of its own, and it went unchecked.

Four suites each carried their own copy of the server thread, and all four
carried the same two defects: a global settings override entered and left on the
server thread's schedule, and a teardown that asked the server to stop without
waiting for it. Both are invisible in a passing run -- an exception on a
background thread is filed as a warning *after* the summary, and a server that
outlives its test just answers the next one's requests.

So the harness is one module now, and these are the properties that let the bugs
hide. They are deterministic on purpose: the race itself is not, which is exactly
why nobody caught it by running the suite.
"""

import pathlib
import re
import threading

import pytest
from django.conf import settings
from testproj import e2e_server
from testproj.e2e_server import serve

pytestmark = pytest.mark.integration


@pytest.fixture
def started_threads(monkeypatch):
    """Every server thread ``serve()`` builds while this fixture is active."""
    built: list[e2e_server.UvicornThread] = []
    original = e2e_server.UvicornThread

    class Recording(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(e2e_server, "UvicornThread", Recording)
    return built


def test_the_server_thread_is_gone_before_the_block_returns(started_threads):
    """Not "was asked to stop" -- gone.

    A server still running holds its port and its application, and the next test
    to ask for a server gets a different port while the old one keeps answering
    anything already pointed at it.
    """
    with serve():
        pass

    assert started_threads, "the recording subclass should have been used"
    assert not started_threads[0].is_alive()


def test_the_debug_override_does_not_outlive_the_block():
    """It is a global override, so leaving it behind changes the next test."""
    before = settings.DEBUG

    with serve():
        assert settings.DEBUG is True

    assert settings.DEBUG == before


def test_a_failure_inside_the_thread_reaches_the_caller(monkeypatch):
    """A server that died must fail the test that needed it, not time out quietly."""

    class Broken(e2e_server.UvicornThread):
        def run(self):
            self.error = RuntimeError("the server could not start")

    monkeypatch.setattr(e2e_server, "UvicornThread", Broken)
    monkeypatch.setattr(e2e_server, "STARTUP_TIMEOUT", 1.0)

    with pytest.raises(AssertionError, match="did not start"):
        with serve():
            pass


def test_no_test_module_runs_a_server_thread_of_its_own():
    """Guard against a fifth copy.

    The pattern that hid the bug was a per-module ``UvicornThread`` with
    ``@override_settings`` on its ``run()``. Anything that needs a live server
    goes through ``testproj.e2e_server`` instead, where the teardown is written
    once and tested here.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    # The harness itself defines the class, and this module names it to subclass it.
    exempt = {pathlib.Path(e2e_server.__file__).resolve(), pathlib.Path(__file__).resolve()}
    pattern = re.compile(r"class UvicornThread\b|@override_settings\([^)]*\)\s*\n\s*def run\(")
    offenders = [
        str(path.relative_to(root))
        for path in list(root.glob("tests/**/*.py")) + list(root.glob("examples/**/*.py"))
        if path.resolve() not in exempt and pattern.search(path.read_text())
    ]

    assert offenders == []


def test_the_port_is_one_the_os_handed_out(started_threads):
    """Not a number somebody picked and hoped was free.

    A collision here fails two or three tests once and passes on the retry, which
    is the least diagnosable failure a suite can have.
    """
    with serve() as base_url:
        thread = started_threads[0]
        assert thread.sock is not None, "the socket is bound before the thread starts"
        assert thread.sock.getsockname()[1] == thread.port
        assert base_url.endswith(f":{thread.port}")


def test_two_servers_never_share_a_port(started_threads):
    with serve() as first:
        with serve() as second:
            assert first != second


def test_nothing_is_left_running_between_two_blocks(started_threads):
    """Two servers in a row, and never two at once."""
    with serve():
        pass
    with serve():
        alive = [t for t in threading.enumerate() if isinstance(t, e2e_server.UvicornThread)]
        assert len(alive) == 1

    assert [t.is_alive() for t in started_threads] == [False, False]
