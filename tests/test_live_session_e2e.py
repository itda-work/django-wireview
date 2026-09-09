"""AC5: crossing a boundary is a full page load, in a real browser.

Three navigation paths reach the same place in the client, and a test that only
clicks a link proves one of them (``docs/design/live-session.md`` §3-3):

- a boosted link click,
- ``popstate`` -- back/forward, where the cached body is morphed on a
  ``requestAnimationFrame`` *before* the fetch that would validate it,
- a server-sent ``push``/``redirect``, which never goes near the click handler.

Plus the case that has to keep working: a boosted move that stays inside the
boundary must still morph, or the check has simply disabled boost.

The probe is a variable on ``window``. A morph leaves it standing; a page load
takes the whole JavaScript context with it, which is exactly the property the
boundary exists to get.
"""

import asyncio
import threading
from random import randint
from time import sleep, time

import pytest
from channels.routing import get_default_application
from django.test import override_settings
from uvicorn.config import Config as UvicornConfig
from uvicorn.main import Server as Uvicorn

pytestmark = pytest.mark.e2e


class UvicornThread(threading.Thread):
    """The ASGI server the browser talks to, for the length of one test.

    ``DEBUG`` is overridden around the *whole* run rather than by decorating
    ``run()``: the decorator enters and leaves a global settings override on the
    thread's own schedule, so a thread still winding down when the next test
    starts restores settings underneath it. The failure that produces --
    ``'override_settings' object has no attribute 'wrapped'`` -- is raised inside
    the thread, where pytest reports it as a warning after a green run.
    """

    def __init__(self, application, host: str, port: int):
        super().__init__()
        self.host = host
        self.port = port
        self.application = application
        self.server: Uvicorn | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        #: Whatever stopped the thread, so the fixture can fail the test with it
        #: instead of letting a dead server look like a slow one.
        self.error: BaseException | None = None

    def run(self):
        try:
            self.loop = asyncio.new_event_loop()
            config = UvicornConfig(self.application, host=self.host, port=self.port, log_level="warning")
            self.server = Uvicorn(config)
            self.server.install_signal_handlers = lambda *args, **kwargs: None
            self.loop.run_until_complete(self.server.serve())
        except BaseException as e:  # noqa: BLE001 - reported through the fixture
            self.error = e
        finally:
            self.server = None

    @property
    def started(self) -> bool:
        return self.server is not None and self.server.started

    def terminate(self):
        if self.server:
            self.server.force_exit = True
            self.server.should_exit = True
            if self.loop:
                self.loop.create_task(self.server.shutdown())


@pytest.fixture(scope="function")
def server():
    host = "127.0.0.1"
    port = randint(9000, 40000)
    thread = UvicornThread(get_default_application(), host, port)
    with override_settings(DEBUG=True):
        thread.start()
        deadline = time() + 15
        while not thread.started:
            if thread.error is not None:
                raise AssertionError(f"the test server did not start: {thread.error!r}")
            if time() > deadline:
                thread.terminate()
                raise AssertionError("the test server did not start within 15s")
            sleep(0.05)

        yield f"http://{host}:{port}"

        thread.terminate()
        # Wait for it: the override above has to outlive the thread that reads the
        # settings, and a server still running into the next test would answer
        # requests meant for that test's own.
        thread.join(timeout=15)
    assert not thread.is_alive(), "the test server did not stop"
    if thread.error is not None:
        raise AssertionError(f"the test server failed: {thread.error!r}")


PROBE = "window.__wireviewProbe"


def open_page(page, server: str, path: str):
    """Load a page, wait for its socket, and plant the survives-a-morph probe."""
    page.goto(f"{server}{path}")
    page.wait_for_selector('[data-is-live="true"]', timeout=5000)
    page.evaluate(f"{PROBE} = 'planted'")


def probe_survived(page) -> bool:
    return page.evaluate(f"{PROBE} ?? null") == "planted"


def wait_for_page(page, name: str) -> None:
    page.wait_for_function(
        "name => document.querySelector('[data-testid=page]')?.textContent.trim() === name",
        arg=name,
        timeout=5000,
    )


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


class TestBoundaryNavigation:
    def test_a_boosted_move_inside_the_boundary_still_morphs(self, page, server):
        """The control. Without this, "everything is a full load" would pass the rest."""
        open_page(page, server, "/livesession/public/")

        page.locator('[data-testid="to-public2"]').click()
        wait_for_page(page, "public2")

        assert probe_survived(page), "two pages outside every boundary are one boundary"

    def test_a_link_click_out_of_the_boundary_reloads(self, page, server):
        page.goto(f"{server}/livesession/sign-in/?next=/livesession/members/")
        page.wait_for_selector('[data-is-live="true"]', timeout=5000)
        page.evaluate(f"{PROBE} = 'planted'")

        page.locator('[data-testid="to-public"]').click()
        wait_for_page(page, "public")

        assert not probe_survived(page), "leaving ls-members must hand the page to the browser"

    def test_going_back_across_the_boundary_reloads(self, page, server):
        """popstate morphs the cached body before the fetch answers, so the
        boundary has to be settled from the history entry itself."""
        page.goto(f"{server}/livesession/sign-in/?next=/livesession/public/")
        page.wait_for_selector('[data-is-live="true"]', timeout=5000)

        page.locator('[data-testid="to-members"]').click()
        wait_for_page(page, "members")
        page.evaluate(f"{PROBE} = 'planted'")

        page.go_back()
        wait_for_page(page, "public")

        assert not probe_survived(page)

    def test_a_server_push_out_of_the_boundary_reloads(self, page, server):
        """``push_to`` never touches the click handler: it calls HistoryCache directly."""
        page.goto(f"{server}/livesession/sign-in/?next=/livesession/staff/")
        page.wait_for_selector('[data-is-live="true"]', timeout=5000)
        page.evaluate(f"{PROBE} = 'planted'")

        page.locator('[data-testid="leave"]').click()
        wait_for_page(page, "public")

        assert not probe_survived(page)

    def test_a_redirect_chain_is_judged_by_where_it_lands(self, page, server):
        """The fetch is for a URL inside the boundary; the response is a page outside it."""
        page.goto(f"{server}/livesession/sign-in/?next=/livesession/members/")
        page.wait_for_selector('[data-is-live="true"]', timeout=5000)
        page.evaluate(f"{PROBE} = 'planted'")

        page.locator('[data-testid="to-bounce"]').click()
        wait_for_page(page, "public")

        assert not probe_survived(page)


class TestBoundaryAccess:
    def test_the_staff_page_refuses_an_anonymous_visitor(self, page, server):
        response = page.goto(f"{server}/livesession/staff/")

        assert "/accounts/login/" in page.url or (response is not None and response.status >= 300)
        assert "staff-only-payload" not in page.content()

    def test_the_staff_page_serves_a_staff_user(self, page, server):
        page.goto(f"{server}/livesession/sign-in/?next=/livesession/staff/")
        page.wait_for_selector('[data-is-live="true"]', timeout=5000)

        assert "staff-only-payload" in page.content()
