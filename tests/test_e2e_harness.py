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

import logging
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


def test_a_failure_after_startup_reaches_the_caller(started_threads):
    """The other half: a server that came up and then fell over.

    Reported on the way out rather than swallowed, so the test that was using it
    fails instead of the next one behaving strangely.
    """
    with pytest.raises(AssertionError, match="fell over"):
        with serve():
            started_threads[0].error = RuntimeError("it fell over mid-test")


def test_a_startup_that_never_finishes_is_cleaned_up_before_it_raises(monkeypatch):
    """The branch the original bug lived in.

    Guarding only the body left the timeout path returning a live thread and then
    restoring settings out from under it -- and the exception it raises hides
    that, because a suite full of green tests reports it as a warning at the end.
    """
    release = threading.Event()

    class NeverReady(e2e_server.UvicornThread):
        @property
        def started(self):
            return False

        def run(self):
            release.wait(timeout=10)

        def terminate(self):
            release.set()

    built: list[NeverReady] = []
    original_init = NeverReady.__init__

    def record(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(NeverReady, "__init__", record)
    monkeypatch.setattr(e2e_server, "UvicornThread", NeverReady)
    monkeypatch.setattr(e2e_server, "STARTUP_TIMEOUT", 0.3)
    before = settings.DEBUG

    try:
        with pytest.raises(AssertionError, match="did not start within"):
            with serve():
                pass
    finally:
        release.set()

    assert settings.DEBUG == before
    assert built and not built[0].is_alive(), "the thread was joined before the settings came back"


def test_a_shutdown_that_does_not_finish_is_reported(monkeypatch, caplog):
    """Reported, not raised over the top of whatever sent us here.

    Python cannot force a thread to end, so the harness accounts for a shutdown
    that did not finish rather than promising one that did. When the test body is
    already failing, that account goes to the log: the original exception is the
    one worth keeping.
    """
    release = threading.Event()

    class Stuck(e2e_server.UvicornThread):
        def terminate(self):
            pass  # ignores the request, so the join times out

        def run(self):
            self.server = _AlreadyServing()
            release.wait(timeout=10)

    monkeypatch.setattr(e2e_server, "UvicornThread", Stuck)
    monkeypatch.setattr(e2e_server, "SHUTDOWN_TIMEOUT", 0.2)

    try:
        with pytest.raises(AssertionError, match="did not stop"):
            with serve():
                pass
    finally:
        release.set()


class _AlreadyServing:
    """Enough of a Uvicorn server for the harness to consider it up."""

    started = True
    force_exit = False
    should_exit = False


def test_the_event_loop_is_closed_with_the_thread(started_threads):
    """A joined thread is not a closed loop, and each one holds a selector."""
    with serve():
        pass

    loop = started_threads[0].loop
    assert loop is not None
    assert loop.is_closed()


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


def test_no_browser_suite_waits_on_its_own_terms():
    """Guard against a sixth copy.

    Five suites each had their own "poll until the page says what I expect", and
    each picked its own budget. A five-second deadline does not catch a bug, it
    reports the machine (#85) -- and none of them could say whether the server
    had died while they waited, which is the one thing that distinguishes a
    broken page from a slow one.

    ``page.wait_for_selector`` is the shape they all had in common, so it belongs
    to ``testproj.e2e_browser`` and nowhere else.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    exempt = {root / "tests" / "testproj" / "e2e_browser.py", pathlib.Path(__file__).resolve()}
    offenders = [
        str(path.relative_to(root))
        for path in list(root.glob("tests/**/*.py")) + list(root.glob("examples/**/*.py"))
        if path.resolve() not in exempt and "page.wait_for_selector(" in path.read_text()
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


def test_what_the_server_logs_is_readable_from_the_test(started_threads):
    """A handler that raises kills the socket and nothing on the page moves again.

    ``receive_json`` has no catch, so the exception goes out through Channels and
    the page simply stops updating -- which, from a browser waiting for an
    element, is indistinguishable from a slow machine (#85). The log is the only
    place the difference exists, so it has to reach the failure message.
    """
    with serve():
        logging.getLogger("wireview").error("handler blew up")
        assert any("handler blew up" in line for line in e2e_server.server_errors())


def test_errors_do_not_leak_between_blocks(started_threads):
    with serve():
        logging.getLogger("wireview").error("from the first block")

    assert e2e_server.server_errors() == []

    with serve():
        assert e2e_server.server_errors() == []


def test_the_collector_is_taken_off_the_root_logger(started_threads):
    """Reading empty is not the same as being gone.

    ``server_errors()`` answers from the current block, so a collector that is
    never detached still reads empty afterwards while every log record in the
    process keeps paying for it -- one more handler per E2E test.
    """
    before = list(logging.getLogger().handlers)

    with serve():
        assert len(logging.getLogger().handlers) == len(before) + 1

    assert logging.getLogger().handlers == before


def test_only_errors_are_collected(started_threads):
    """A warning is not a failure. Reporting one under a timeout sends the reader chasing it."""
    with serve():
        logging.getLogger("wireview").warning("a rejoin was refused")
        assert e2e_server.server_errors() == []
