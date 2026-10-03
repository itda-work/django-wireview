"""What a page gets back when its socket comes back (#168).

A component's state lives in the ``data-state`` its last render put on the page,
signed. A reconnect joins every component again from that attribute, so the
server that takes the socket -- the same one, the same one restarted, or another
worker -- needs only the signing key, not the instance that went away.

What is checked here, in a browser:

- the state after a reconnect is the last render's, not the page load's;
- ``data-state`` changes with every render that changes the state, and stays the
  same token on a render that does not;
- a restarted server process, and another process with the same settings, take
  the page back where it was without reloading it;
- what the user typed and had not sent is still in the fields.

The socket is cut from inside the page (``OFFLINE_SHIM``). The servers that stop
or change are processes of their own (``testproj.server_process``); the rest use
the in-process server. None of it needs a broker -- one connection talks to one
server -- so every layer runs it.

What the reconnect replays of a form (``wire-auto-recover``) and of hooks is
tests/test_offline_e2e.py's.

Fixture: tests/testproj/reconnectprobe/.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import OFFLINE_SHIM, expect_text, open_live, wait_live
from testproj.e2e_server import serve
from testproj.server_process import free_port, launch_uvicorn, start_uvicorn, stop_process, wait_until_serving

pytestmark = pytest.mark.e2e

LIVE = "#rbox[data-is-live='true']:not(.wireview-disconnected)"

#: Every server process together, well inside the per-test limit.
READY_TIMEOUT = 30.0


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def quick_reconnect(page) -> None:
    """Retry every 100 ms instead of the 1 to 5 seconds a page waits by default.

    Rewritten in the page as it arrives, because a server in another process
    reads its own settings, which a test cannot override.
    """

    def fulfil(route):
        response = route.fetch()
        body = response.text()
        for name, value in (("min-delay", "100"), ("jitter", "0"), ("max-delay", "100"), ("grow-factor", "1")):
            body = re.sub(rf'data-{name}="[^"]*"', f'data-{name}="{value}"', body)
        route.fulfill(response=response, body=body)

    page.route(re.compile(r"/reconnectprobe/(\?.*)?$"), fulfil)


class Link:
    """The page's WebSocket, cut, restored and moved on demand."""

    def __init__(self, page) -> None:
        self.page = page
        page.add_init_script(OFFLINE_SHIM)
        quick_reconnect(page)

    def cut(self) -> None:
        self.page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
        expect(self.page.locator("#rbox")).to_have_class(re.compile("wireview-disconnected"))

    def restore(self, port: int | None = None) -> None:
        self.page.evaluate("port => { window.__link.offline = false; window.__link.port = port; }", port)
        wait_live(self.page, LIVE)

    def open(self, url: str) -> None:
        open_live(self.page, url, selector=LIVE)
        # A marker a reload would lose
        self.page.evaluate("window.__samePage = true")

    @property
    def same_page(self) -> bool:
        return self.page.evaluate("window.__samePage === true")


def by(page, testid: str):
    return page.get_by_test_id(testid)


def signed_state(page) -> str:
    return page.locator("#rbox").get_attribute("data-state") or ""


def bump_to(page, count: int) -> None:
    by(page, "bump").click()
    expect_text(by(page, "count"), str(count))


# --- the same server ---------------------------------------------------------------------


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture
def link(page, server):
    link = Link(page)
    link.open(f"{server}/reconnectprobe/")
    return link


def test_a_reconnect_brings_back_the_state_of_the_last_render(link):
    page = link.page
    for count in (1, 2, 3):
        bump_to(page, count)
    by(page, "note").fill("kept")
    by(page, "note").press("Enter")
    expect_text(by(page, "saved"), "kept")

    link.cut()
    link.restore()

    # The page load said 0 and ""; the join rebuilt the component from the last render
    expect_text(by(page, "count"), "3")
    expect_text(by(page, "saved"), "kept")
    assert link.same_page, "a reconnect, not a reload"
    # And the instance it built goes on from there
    bump_to(page, 4)


def test_every_render_that_changes_the_state_signs_it_again(link):
    page = link.page
    seen = [signed_state(page)]
    for count in (1, 2):
        bump_to(page, count)
        seen.append(signed_state(page))
    assert len(set(seen)) == 3, "each render that changed the state carried a new token"


def test_a_render_that_changes_nothing_keeps_the_signed_state(link):
    page = link.page
    bump_to(page, 1)
    token = signed_state(page)

    by(page, "nothing").click()
    by(page, "nothing").click()
    # Both acknowledgements are in once a later event's answer is: one socket, in order.
    # That answer's render is the only one that changes the state, so compare before it.
    page.evaluate(
        """() => {
          window.__tokens = [];
          new MutationObserver(() => window.__tokens.push(
            document.getElementById('rbox').dataset.state)).observe(
            document.getElementById('rbox'), {attributes: true, attributeFilter: ['data-state']});
        }"""
    )
    bump_to(page, 2)

    tokens = page.evaluate("window.__tokens")
    assert tokens and tokens[0] != token, "the bump signed a new state"
    assert len(tokens) == 1, f"the renders that changed nothing left the token alone: {tokens}"


def test_what_the_user_typed_and_did_not_send_is_still_there_after_the_reconnect(link):
    """The join's render morphs the component, and a field the user edited keeps its
    value (values.mjs): one bound to state whose edit was not sent, and one that is
    not state at all."""
    page = link.page
    by(page, "note").fill("not sent yet")
    by(page, "scratch").fill("only on the page")
    by(page, "scratch").focus()

    link.cut()
    link.restore()

    expect(by(page, "note")).to_have_value("not sent yet")
    expect(by(page, "scratch")).to_have_value("only on the page")
    expect_text(by(page, "saved"), "")
    assert link.same_page


# --- another server: restarted, or another worker ---------------------------------------


@pytest.fixture
def workers(tmp_path):
    """Two server processes with the same settings -- the signing key included."""
    pytest.importorskip("uvicorn")
    ports = [free_port(), free_port()]
    procs = start_uvicorn(ports, tmp_path, READY_TIMEOUT)
    running = dict(zip(ports, procs))
    try:
        yield running
    finally:
        for log in tmp_path.glob("worker-*.log"):
            if "Traceback" in log.read_text():
                print(f"--- {log.name} ---\n{log.read_text()}")
        for proc in running.values():
            stop_process(proc)


def test_a_restarted_server_takes_the_page_back_where_it_was(page, workers, tmp_path):
    """A deploy: the process that rendered the page and held its socket is gone, and
    a new one -- with no memory of the component -- answers on the same address."""
    port = next(iter(workers))
    link = Link(page)
    link.open(f"http://127.0.0.1:{port}/reconnectprobe/")
    bump_to(page, 1)
    bump_to(page, 2)
    first_pid = by(page, "pid").inner_text()

    stop_process(workers[port])
    expect(page.locator("#rbox")).to_have_class(re.compile("wireview-disconnected"))
    proc, log = launch_uvicorn(port, tmp_path)
    workers[port] = proc
    wait_until_serving(port, proc, log, READY_TIMEOUT)
    wait_live(page, LIVE)

    expect(by(page, "pid")).not_to_have_text(first_pid)
    expect_text(by(page, "count"), "2")
    assert link.same_page, "the page was not reloaded"
    bump_to(page, 3)


def test_another_worker_with_the_same_settings_takes_the_page_back(page, workers):
    """Behind a load balancer the next socket can land anywhere. The worker that takes
    it never saw the page, and needs nothing but the signing key."""
    first, second = workers
    link = Link(page)
    link.open(f"http://127.0.0.1:{first}/reconnectprobe/")
    bump_to(page, 1)
    first_pid = by(page, "pid").inner_text()

    link.cut()
    link.restore(port=second)

    expect(by(page, "pid")).not_to_have_text(first_pid)
    expect_text(by(page, "count"), "1")
    assert link.same_page
    bump_to(page, 2)
