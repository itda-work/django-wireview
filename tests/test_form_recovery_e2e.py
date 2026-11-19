"""What a new instance gets back of the forms the user typed into (#245, GAP-049).

Two things build a new instance for a component that was live, from the state
its element carries: a reconnect (the network went down) and an ``error``
during an event (the instance that raised is gone; the page joins its element
again). Either way a ``wire-auto-recover`` form gives the new instance what it
holds, once, when its join has landed and its render is on the page:

- to the new instance and nobody else: not to another component on the page,
  not twice, not to the instance a socket opened for another identity makes
  (``renew``, #209);
- only the same form: one the new render dropped, gave another id or turned
  off is not recovered, and ``wire-auto-recover="false"`` is never;
- not its files: an upload input's file went with the instance it was chosen
  for (#137), and a plain file input sends nothing;
- a recovery that raises is rolled back without recovering again.

``ignore`` is a handler's name, as it always was.

Fixture: tests/testproj/recoverprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import OFFLINE_SHIM, expect_text, open_live, wait_live
from testproj.e2e_server import serve
from testproj.recoverprobe import live

pytestmark = pytest.mark.e2e

LIVE = "#box[data-is-live='true']:not(.wireview-disconnected)"
#: Long enough for a second recovery, or a loop, to show
QUIET_MS = 1500


@pytest.fixture(scope="function")
def server():
    live.reset()
    with serve() as base_url:
        yield base_url
    live.reset()


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


def tag(page, id_: str) -> str:
    return by(page, f"{id_}-tag").inner_text()


def heard(id_: str | None = None, handler: str | None = None) -> list[tuple]:
    return [h for h in live.HEARD if (id_ is None or h[0] == id_) and (handler is None or h[2] == handler)]


class Link:
    """The page's WebSocket, cut and restored on demand."""

    def __init__(self, page) -> None:
        self.page = page
        page.add_init_script(OFFLINE_SHIM)

    def cut(self) -> None:
        self.page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
        expect(self.page.locator("#box")).to_have_class(re.compile("wireview-disconnected"))

    def restore(self) -> None:
        self.page.evaluate("() => { window.__link.offline = false; }")


@pytest.fixture
def link(page, server):
    link = Link(page)
    open_live(page, f"{server}/recoverprobe/", selector=LIVE)
    page.evaluate("window.__samePage = true")
    return link


def crash(page, id_: str = "box") -> None:
    """An event whose handler raises. Clicked from a script, so the focused field
    keeps its focus and sends no change of its own on the way."""
    page.evaluate(f"() => document.querySelector('[data-testid={id_}-boom]').click()")


class Trigger:
    """What builds the new instance: the network back, or an error's rollback."""

    def __init__(self, kind: str, link: Link) -> None:
        self.kind = kind
        self.link = link

    def before_typing(self) -> None:
        if self.kind == "reconnect":
            self.link.cut()

    def rebuild(self) -> None:
        if self.kind == "reconnect":
            self.link.restore()
        else:
            crash(self.link.page)


@pytest.fixture(params=["reconnect", "error"])
def trigger(request, link):
    return Trigger(request.param, link)


def type_everything(page) -> None:
    """Every form of box and other, the focused one last: what only the page holds."""
    by(page, "box-draft").fill("half a sentence")
    by(page, "box-leaf-draft").fill("leafy")
    by(page, "box-note").fill("noted")
    by(page, "box-off").fill("nope")
    by(page, "other-draft").fill("theirs")
    by(page, "box-city").fill("Seoul")


def new_instance(page, id_: str, old: str) -> str:
    expect(by(page, f"{id_}-tag")).not_to_have_text(old)
    expect(by(page, f"{id_}-tag")).not_to_have_text("")
    wait_live(page, LIVE)
    return tag(page, id_)


def test_each_form_goes_back_once_to_the_new_instance(trigger):
    page = trigger.link.page
    box, other = tag(page, "box"), tag(page, "other")
    leaf = tag(page, "box-leaf")
    trigger.before_typing()
    type_everything(page)
    trigger.rebuild()

    expect_text(by(page, "box-kept"), "half a sentence")
    expect_text(by(page, "box-city-value"), "Seoul")
    expect_text(by(page, "box-ignored"), "noted")
    expect_text(by(page, "box-leaf-kept"), "leafy")
    new_box = new_instance(page, "box", box)
    new_leaf = tag(page, "box-leaf")
    assert new_leaf != leaf
    page.wait_for_timeout(QUIET_MS)

    # Once each, to the instance the join made -- the LiveComponent's own, which its root's join built
    assert heard("box", "keep") == [("box", new_box, "keep", [("draft", "half a sentence")], "")]
    assert heard("box", "set_city") == [("box", new_box, "set_city", "Seoul", "")]
    # ignore is a handler's name, as it always was
    assert heard("box", "ignore") == [("box", new_box, "ignore", "noted", "")]
    assert heard("box-leaf", "keep") == [("box-leaf", new_leaf, "keep", [("draft", "leafy")], "")]
    # Turned off: never. (Leaving the field sent its change to the old instance, online.)
    assert [h for h in heard(handler="set_off") if h[1] in (new_box, new_leaf)] == []
    if trigger.kind == "reconnect":
        assert heard(handler="set_off") == []
    if trigger.kind == "reconnect":
        # Every component joined again, each its own forms
        new_other = new_instance(page, "other", other)
        assert heard("other", "keep") == [("other", new_other, "keep", [("draft", "theirs")], "")]
    else:
        # The other component kept its instance, and nothing of it was recovered
        assert tag(page, "other") == other
        assert heard("other") == []
        expect_text(by(page, "other-kept"), "")
    # The new instance answers as ever
    by(page, "box-bump").click()
    expect_text(by(page, "box-count"), "1")


@pytest.mark.parametrize(
    "layout",
    [{"shown": False}, {"form_id": "renamed"}, {"recover": "false"}],
    ids=["dropped", "renamed", "turned-off"],
)
def test_a_form_the_new_render_does_not_draw_the_same_is_not_recovered(trigger, layout):
    page = trigger.link.page
    old = tag(page, "box")
    trigger.before_typing()
    by(page, "box-draft").fill("half a sentence")
    by(page, "box-city").fill("Seoul")
    live.LAYOUT.update(layout)
    trigger.rebuild()

    # The control: the city form is the same, and comes back
    expect_text(by(page, "box-city-value"), "Seoul")
    new_instance(page, "box", old)
    page.wait_for_timeout(QUIET_MS)
    assert heard("box", "keep") == []
    expect_text(by(page, "box-kept"), "")


def test_files_are_not_recovered(trigger, tmp_path):
    page = trigger.link.page
    chosen = tmp_path / "notes.txt"
    chosen.write_text("x", encoding="utf-8")
    old = tag(page, "box")
    # The upload input's file is registered with the instance that is live
    by(page, "box-photo").set_input_files(str(chosen))
    expect(by(page, "box-entries").locator("li")).to_have_text(["notes.txt"])
    by(page, "box-attachment").set_input_files(str(chosen))
    trigger.before_typing()
    by(page, "box-draft").fill("half a sentence")
    trigger.rebuild()

    expect_text(by(page, "box-kept"), "half a sentence")
    new_box = new_instance(page, "box", old)
    page.wait_for_timeout(QUIET_MS)
    # Neither file input is in what came back
    assert heard("box", "keep") == [("box", new_box, "keep", [("draft", "half a sentence")], "")]
    # The file went with the old instance; the new one has none
    expect(by(page, "box-entries").locator("li")).to_have_count(0)


def test_a_recovery_that_raises_is_not_recovered_again(server, page):
    live.LAYOUT["explode"] = True
    open_live(page, f"{server}/recoverprobe/", selector=LIVE)
    old = tag(page, "box")
    by(page, "box-fuse").fill("lit")
    crash(page)

    # The rollback recovered: the draft form's keep, and explode, which raised.
    # Its rollback recovers nothing: one explode, and the page answers.
    new_instance(page, "box", old)
    page.wait_for_timeout(QUIET_MS)
    assert [h[3] for h in heard("box", "explode")] == ["lit"]
    by(page, "box-bump").click()
    expect_text(by(page, "box-count"), "1")
    page.wait_for_timeout(QUIET_MS)
    assert len(heard("box", "explode")) == 1

    # The user did something since: the next error recovers again, once
    by(page, "box-fuse").fill("lit again")
    crash(page)
    page.wait_for_timeout(QUIET_MS)
    assert [h[3] for h in heard("box", "explode")] == ["lit", "lit again"]
    by(page, "box-bump").click()
    expect_text(by(page, "box-count"), "2")


# --- a socket opened for another identity (#209) ---------------------------------------


def other_tab(context, server: str, path: str) -> None:
    page = context.new_page()
    page.goto(f"{server}{path}")
    expect(by(page, "done")).to_be_visible()
    page.close()


@pytest.mark.parametrize(
    "change, outcome",
    [("/identityprobe/rotate/", "renew"), ("/identityprobe/sign-in/grace/", "reload")],
    ids=["rotate-csrf", "another-user"],
)
def test_a_reconnect_for_another_identity_recovers_nothing(browser, server, change, outcome):
    """The page was drawn for whoever the cookies named before; the reconnect's socket
    is the cookies' now. The server answers its joins with ``renew``; the socket that
    opens joins as the new identity (the CSRF secret alone changed) or reloads (the
    login did). What the user typed for the old identity goes to neither."""
    context = browser.new_context()
    other_tab(context, server, "/identityprobe/sign-in/ada/")
    page = context.new_page()
    link = Link(page)
    sockets: list[str] = []
    page.on("websocket", lambda socket: sockets.append(socket.url))
    open_live(page, f"{server}/recoverprobe/", selector=LIVE)
    page.evaluate("window.__samePage = true")
    link.cut()
    type_everything(page)
    other_tab(context, server, change)
    link.restore()

    if outcome == "renew":
        # The original, the reconnect's, the renewal's: joined there, on the same page
        expect(page.locator(LIVE)).to_be_visible()
        for _ in range(100):
            if len(sockets) >= 3:
                break
            page.wait_for_timeout(100)
        wait_live(page, LIVE)
        assert page.evaluate("window.__samePage === true")
    else:
        for _ in range(100):
            if page.evaluate("window.__samePage !== true"):
                break
            page.wait_for_timeout(100)
        assert page.evaluate("window.__samePage !== true"), "the page reloads"
        wait_live(page, LIVE)
    page.wait_for_timeout(QUIET_MS)
    assert len(sockets) >= 3, sockets
    assert heard(handler="keep") == []
    assert heard(handler="set_city") == []
    assert heard(handler="ignore") == []
    # The instance answers -- clicked from a script: leaving the city field would send its change
    page.evaluate("() => document.querySelector('[data-testid=box-bump]').click()")
    expect_text(by(page, "box-count"), "1")
    context.close()


# --- a server that does not say `joined` -----------------------------------------------

#: Every frame as a server older than ``joined`` (vsn 5) would send it: the
#: version it announces is 4, and no ``joined`` comes. The page then takes the
#: join's render as the sign the join landed.
OLD_SERVER_SHIM = """
(() => {
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    addEventListener(type, listener, options) {
      if (type !== "message") return super.addEventListener(type, listener, options);
      return super.addEventListener(type, (event) => {
        const message = JSON.parse(event.data);
        if (message.command === "joined") return;
        if (typeof message.payload?.vsn === "number") message.payload.vsn = 4;
        listener.call(this, new MessageEvent("message", { data: JSON.stringify(message) }));
      }, options);
    }
  };
})();
"""


@pytest.mark.parametrize("layout", [{}, {"shown": False}], ids=["kept", "dropped"])
def test_a_server_without_joined_recovers_after_the_joins_render(server, page, layout):
    """Looked for in what the join's render drew, not in the page before it."""
    page.add_init_script(OLD_SERVER_SHIM)
    link = Link(page)
    open_live(page, f"{server}/recoverprobe/", selector=LIVE)
    old = tag(page, "box")
    link.cut()
    by(page, "box-draft").fill("half a sentence")
    by(page, "box-city").fill("Seoul")
    live.LAYOUT.update(layout)
    link.restore()

    expect_text(by(page, "box-city-value"), "Seoul")
    new_box = new_instance(page, "box", old)
    page.wait_for_timeout(QUIET_MS)
    if layout:
        assert heard("box", "keep") == []
    else:
        assert heard("box", "keep") == [("box", new_box, "keep", [("draft", "half a sentence")], "")]
