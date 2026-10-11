"""A boosted move after another tab changed who is logged in (#209).

A boosted navigation keeps the WebSocket. The page it fetches is rendered for
whoever the browser's cookies name *now*; the socket was opened under the
cookies of its handshake. When another tab of the same browser logs in, logs
out, logs in as someone else or rotates the CSRF secret in between, the new
page's components used to join under the socket's old identity: one screen,
two people -- the Django part of the page for one, the components (their
``user`` and the token their forms post with) for the other.

Each test changes the identity in a second tab, moves the first with a boosted
link, and checks that the components agree with the page around them. The
control checks that a move with no change keeps the one socket it had.

Fixture: tests/testproj/identityprobe/.
"""

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import WAIT_TIMEOUT, expect_text, open_live
from testproj.e2e_server import serve
from testproj.identityprobe import views

pytestmark = pytest.mark.e2e

PROBE = "window.__wireviewProbe"
POST_URL = "/identityprobe/post/"


@pytest.fixture(scope="function")
def server():
    views.POSTED.clear()
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


class Tab:
    """A page that counts the sockets it opens and carries a probe a page load would lose."""

    def __init__(self, context, server: str, path: str) -> None:
        self.server = server
        self.page = context.new_page()
        self.sockets: list[str] = []
        self.page.on("websocket", lambda socket: self.sockets.append(socket.url))
        open_live(self.page, f"{server}{path}")
        self.page.evaluate(f"{PROBE} = 'planted'")

    def move(self, link: str, name: str) -> None:
        """Follow a boosted link and wait for the page it names."""
        by(self.page, f"to-{link}").click()
        expect_text(by(self.page, "page"), name)

    def settle(self) -> None:
        """Wait until the card answers an event, on whichever socket it ends up joined on.

        An event clicked while the page is still joining may reach no instance;
        it is clicked again. Any render after the join's draws the instance's
        own ``user`` and token, which is what the tests read.
        """
        bump = by(self.page, "bump")
        for _ in range(int(WAIT_TIMEOUT)):
            bump.click()
            try:
                expect(bump).not_to_have_text("0", timeout=1000)
                return
            except AssertionError:
                continue
        expect(bump).not_to_have_text("0", timeout=1000)

    def who(self) -> tuple[str, str]:
        """Who the page says is logged in, and who the component says."""
        return by(self.page, "page-who").inner_text(), by(self.page, "card-who").inner_text()

    def submit(self, name: str) -> int:
        with self.page.expect_response(lambda response: response.url.endswith(POST_URL)) as answered:
            by(self.page, f"{name}-submit").click()
        return answered.value.status

    @property
    def loaded_again(self) -> bool:
        return self.page.evaluate(f"{PROBE} ?? null") != "planted"


def other_tab(context, server: str, path: str) -> None:
    """Do something to the browser's cookies from another tab of the same context."""
    page = context.new_page()
    page.goto(f"{server}{path}")
    expect(by(page, "done")).to_be_visible()
    page.close()


def agree(tab: Tab, name: str, sockets: int) -> None:
    """The page and its component name the same person, and the component's form posts as them.

    ``sockets`` is how many the tab opened by then. The form's post is a page
    load, so whether the move was one is asked before it.
    """
    tab.settle()
    expect_text(by(tab.page, "card-who"), name)
    assert tab.who() == (name, name)
    assert len(tab.sockets) == sockets
    assert not tab.loaded_again
    assert tab.submit("card") == 200
    assert views.POSTED[-1] == ("card", "" if name == "anonymous" else name)


@pytest.mark.parametrize("signed_in", [None, "ada"], ids=["anonymous", "logged-in"])
@pytest.mark.parametrize("pages", [("a", "b"), ("inside-a", "inside-b")], ids=["no-boundary", "boundary"])
def test_a_move_with_no_change_keeps_its_socket(browser, server, pages, signed_in):
    """The control: a boosted move is still a morph over the socket it had."""
    context = browser.new_context()
    if signed_in:
        other_tab(context, server, f"/identityprobe/sign-in/{signed_in}/")
    start, destination = pages
    tab = Tab(context, server, f"/identityprobe/{start}/")
    tab.move(destination, destination)
    tab.move(start, start)
    agree(tab, signed_in or "anonymous", sockets=1)
    context.close()


@pytest.mark.parametrize(
    ("signed_in", "change", "after"),
    [
        (None, "sign-in/ada/", "ada"),
        ("ada", "sign-out/", "anonymous"),
        ("ada", "sign-in/grace/", "grace"),
        ("ada", "rotate/", "ada"),
    ],
    ids=["log-in", "log-out", "another-user", "rotate-csrf"],
)
@pytest.mark.parametrize("pages", [("a", "b"), ("inside-a", "inside-b")], ids=["no-boundary", "boundary"])
def test_a_move_after_another_tab_changed_the_identity(browser, server, signed_in, change, after, pages):
    """The new page's components take the identity of the page they are on, over a new socket."""
    context = browser.new_context()
    if signed_in:
        other_tab(context, server, f"/identityprobe/sign-in/{signed_in}/")
    start, destination = pages
    tab = Tab(context, server, f"/identityprobe/{start}/")
    before = signed_in or "anonymous"
    assert tab.who() == (before, before)

    other_tab(context, server, f"/identityprobe/{change}")
    tab.move(destination, destination)
    # A new handshake, not a page load: the page the navigation fetched stays
    agree(tab, after, sockets=2)
    context.close()
