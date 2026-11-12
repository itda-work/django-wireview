"""Uploads, every way in, in a real browser (#110).

The drop zone, the preview and external uploads had no test; the file input was
only ever used for one file. Fixture: tests/testproj/fileprobe/.
"""

import asyncio
import json
import re
import threading

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import INBOX_SHIM, OFFLINE_SHIM, WAIT_TIMEOUT, expect_count, expect_text, open_live, wait_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

# A 1x1 PNG: the magic-byte check wants a real one.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def probe(page, server):
    open_live(page, f"{server}/fileprobe/")
    return page


def by(page, testid: str):
    return page.get_by_test_id(testid)


def text_file(name: str, body: str) -> dict:
    return {"name": name, "mimeType": "text/plain", "buffer": body.encode()}


def test_one_upload_after_another_past_max_entries(probe):
    # max_entries=1 counts uploads in flight. A consumed one is done: read() used to
    # leave it "completed", holding the only slot, and the second upload was refused.
    for n, name in enumerate(["a.txt", "b.txt", "c.txt"], start=1):
        by(probe, "files").set_input_files(text_file(name, "x" * n))
        expect_count(by(probe, "received").locator("li"), n)
    expect(by(probe, "received")).to_have_text("a.txt:1b.txt:2c.txt:3")


@pytest.fixture
def held_config(monkeypatch):
    """Hold every upload config at the session until the test sets the returned event (#137).

    A config reaches the browser after the render that makes the page live: it
    is sent from a task, through the channel layer. Delaying every config by
    500 ms failed the five tests above on the memory layer the way they failed on
    CI's NATS lane, while they passed on every laptop; that the broker's trip was
    the delay on CI is the likeliest explanation, not a confirmed one. What is
    certain is what the page did: a file chosen before the config was dropped.

    Held where the session hands it to the socket, the config also stays behind
    whatever the page sends meanwhile -- the session reads one socket in order --
    so a test can end the instance before its config arrives.
    """
    from wireview.session import WireviewSession

    release = threading.Event()
    forward = WireviewSession.component_upload_op

    async def held(self, op, upload, ref=None, **data):
        if op == "config":
            await asyncio.to_thread(release.wait, WAIT_TIMEOUT)
        await forward(self, op, upload, ref, **data)

    monkeypatch.setattr(WireviewSession, "component_upload_op", held)
    return release


@pytest.fixture
def registered(monkeypatch):
    """The names of the files the page registers, in the order the server hears them."""
    from wireview.session import WireviewSession

    names: list[str] = []
    register = WireviewSession.command_upload_register

    async def recording(self, id, name, entries):
        names.extend(entry["name"] for entry in entries)
        await register(self, id, name, entries)

    monkeypatch.setattr(WireviewSession, "command_upload_register", recording)
    return names


def test_a_file_chosen_before_the_upload_config_arrives_is_uploaded_when_it_does(held_config, page, server):
    from testproj.fileprobe.views import RECEIVED

    RECEIVED.clear()
    open_live(page, f"{server}/fileprobe/")
    by(page, "files").set_input_files(text_file("early.txt", "early"))
    by(page, "outside").set_input_files(text_file("e.txt", "external"))

    held_config.set()

    expect_text(by(page, "received").locator("li"), "early.txt:5")
    expect_text(by(page, "external-done"), "e.txt")


def test_the_upload_button_opens_the_picker_before_the_config_arrives(held_config, page, server):
    # The picker opens only inside the click's user activation; the button used
    # to do nothing at all until the config had come.
    open_live(page, f"{server}/fileprobe/")
    with page.expect_file_chooser(timeout=WAIT_TIMEOUT * 1000) as chooser:
        by(page, "pick").click()
    chooser.value.set_files(text_file("picked.txt", "picked"))

    held_config.set()

    expect_text(by(page, "received").locator("li"), "picked.txt:6")


def test_a_file_held_for_a_component_that_left_is_not_registered_by_its_late_config(
    held_config, registered, page, server
):
    open_live(page, f"{server}/fileprobe/")
    by(page, "files").set_input_files(text_file("gone.txt", "gone"))
    by(page, "to-other").click()
    expect_text(by(page, "page"), "other")

    # The configs reach a page the component has left
    held_config.set()

    # Whatever they made the page send reaches the server before this join does
    by(page, "to-probe").click()
    wait_live(page)
    by(page, "files").set_input_files(text_file("new.txt", "new"))
    expect_text(by(page, "received").locator("li"), "new.txt:3")
    assert registered == ["new.txt"]


def test_a_file_held_for_an_instance_a_new_join_replaced_does_not_pass_to_the_new_one(
    held_config, registered, page, server
):
    open_live(page, f"{server}/fileprobe/")
    by(page, "files").set_input_files(text_file("old.txt", "old"))
    # The same component id on the next page: the server retires the old
    # instance and mounts a new one under it
    by(page, "again").click()
    expect_text(by(page, "page"), "again")

    held_config.set()

    wait_live(page)
    by(page, "files").set_input_files(text_file("new.txt", "new"))
    expect_text(by(page, "received").locator("li"), "new.txt:3")
    assert registered == ["new.txt"]


@pytest.fixture
def late_old_config(monkeypatch):
    """Hold the first instance's upload configs in the task that sends them (#137).

    Unlike ``held_config`` this holds nothing at the session, which goes on
    reading the socket: the page's next join is handled, the new instance answers
    it and sends its own config, and only then does the old instance's mail reach
    the session -- the order a slow broker can produce. Returns ``(release,
    handled)``: set the first to let the mail go; the second is set once the
    session has dealt with every piece of it.
    """
    from wireview.core.meta import WireviewMeta
    from wireview.session import WireviewSession

    release = threading.Event()
    handled = threading.Event()
    first: list[int] = []
    pending = [0]
    in_flight: set[asyncio.Task] = set()
    send = WireviewMeta.send_upload_op
    forward = WireviewSession.component_upload_op

    async def deliver_later(wire, op, owner):
        await asyncio.to_thread(release.wait, WAIT_TIMEOUT)
        await send(wire, op, owner)

    async def holding(self, op, owner):
        if op.op == "config" and (not first or first[0] == self.instance):
            first[:1] = [self.instance]
            pending[0] += 1
            # Handed off, as a broker holds a message it accepted: retiring the
            # instance cancels its tasks, not what they already sent
            task = asyncio.create_task(deliver_later(self, op, owner))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
            return
        await send(self, op, owner)

    async def counting(self, op, upload, ref=None, **data):
        await forward(self, op, upload, ref, **data)
        if first and data.get("instance") == first[0]:
            pending[0] -= 1
            if pending[0] == 0:
                handled.set()

    monkeypatch.setattr(WireviewMeta, "send_upload_op", holding)
    monkeypatch.setattr(WireviewSession, "component_upload_op", counting)
    return release, handled


#: Counts the chunk requests the page makes from here on. ``startUpload`` calls
#: fetch in the same turn as the ``registered`` op that starts it.
COUNT_CHUNKS = """() => {
  window.__chunks = 0;
  const send = window.fetch;
  window.fetch = (url, init) => {
    if (String(url).includes("__wireview_upload__")) window.__chunks += 1;
    return send(url, init);
  };
}"""


def test_a_late_config_from_the_replaced_instance_does_not_decide_the_new_ones_upload(
    late_old_config, registered, page, server
):
    # The old instance uploads "files" at once, the new one only when asked. The
    # old config, forwarded after the new join's answer, was taken for the new
    # instance's and started the upload the new instance did not want.
    release, handled = late_old_config
    open_live(page, f"{server}/fileprobe/")
    by(page, "manual").click()
    expect_text(by(page, "page"), "manual")
    wait_live(page)

    release.set()
    assert handled.wait(WAIT_TIMEOUT)

    page.evaluate(COUNT_CHUNKS)
    by(page, "files").set_input_files(text_file("new.txt", "new"))
    # Rendered after the registered op, which is where an upload would start
    expect_text(by(page, "entry"), "new.txt")
    assert page.evaluate("window.__chunks") == 0
    assert registered == ["new.txt"]


#: How many upload configs for ``#probe`` the page holds, not yet handed to it
HELD_CONFIGS = (
    "window.__inbox.held.filter((m) => m.command === 'upload_op' && m.payload.op === 'config'"
    " && m.payload.id === 'probe' && m.payload.upload === 'files').length"
)


def test_a_file_chosen_as_a_replaced_joins_answer_arrives_goes_to_the_join_that_replaced_it(registered, page, server):
    # #139: the page sent a join, and another under the same id before the
    # first was answered. The first answer named its instance as current, and
    # a file chosen then went to that instance's config -- which the second
    # answer ended, with the file.
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/fileprobe/")
    # Answered: the page knows the server takes a join's ref
    page.wait_for_function("window.__inbox.seen.some((m) => m.command === 'upload_op' && m.payload.op === 'config')")

    page.evaluate("window.__inbox.holding = true")
    by(page, "again").click()
    expect_text(by(page, "page"), "again")
    page.wait_for_function(f"{HELD_CONFIGS} >= 1")
    by(page, "twice").click()
    expect_text(by(page, "page"), "twice")
    page.wait_for_function(f"{HELD_CONFIGS} >= 2")

    # The first join's answer and its instance's config reach the page; then the user chooses
    page.evaluate(
        """() => {
          const first = window.__inbox.held.findIndex((m) => m.command === 'upload_op'
            && m.payload.op === 'config' && m.payload.id === 'probe' && m.payload.upload === 'files');
          window.__inbox.release(first + 1);
        }"""
    )
    # The page this far has no live render of the new DOM, so no file input:
    # the button opens the picker without a config (#137)
    with page.expect_file_chooser(timeout=WAIT_TIMEOUT * 1000) as chooser:
        by(page, "pick").click()
    chooser.value.set_files(text_file("window.txt", "window"))
    page.evaluate("window.__inbox.release()")

    expect_text(by(page, "received").locator("li"), "window.txt:6")
    assert registered == ["window.txt"]


def test_a_preview_url_the_page_asked_for_ends_with_the_component(probe):
    by(probe, "images").set_input_files({"name": "p.png", "mimeType": "image/png", "buffer": PNG})
    expect(by(probe, "preview")).to_have_attribute("src", re.compile(r"^blob:"))
    url = probe.evaluate(
        """() => {
          const img = document.querySelector('[data-testid="preview"]');
          const [name, ref] = img.getAttribute("wire-preview").split(":");
          return window.wireview.getPreviewUrl(img, name, ref);
        }"""
    )
    loads = "(url) => fetch(url).then(() => true, () => false)"
    assert probe.evaluate(loads, url)

    by(probe, "to-other").click()
    # The component left in the same frame that swapped the page in
    expect_text(by(probe, "page"), "other")

    # And the File the URL points at was released with its uploads
    assert not probe.evaluate(loads, url)


def test_a_live_component_on_the_first_page_uploads_with_its_config(page, server):
    # #142: its instance is named in the parent's join answer, not in a render
    # of its own. Without this one, the test below was the only one to see a
    # LiveComponent upload, and it could not tell the two paths apart.
    open_live(page, f"{server}/fileprobe/nested/")
    by(page, "child-files").set_input_files(text_file("first.txt", "first"))
    expect_text(by(page, "child-received").locator("li"), "first.txt:5")


def test_a_live_component_shown_again_under_its_id_uploads_with_its_new_config(page, server):
    # Hidden, the child left: the page ended its uploads and closed the id to
    # configs. A LiveComponent never sends a join -- its parent's render makes
    # the new instance -- so nothing opened the id again, and the new
    # instance's config was dropped: the file waited for ever.
    open_live(page, f"{server}/fileprobe/nested/")
    by(page, "toggle").click()
    expect_text(by(page, "shown"), "False")
    expect_count(by(page, "child-files"), 0)
    by(page, "toggle").click()
    expect_text(by(page, "shown"), "True")

    by(page, "child-files").set_input_files(text_file("again.txt", "again"))
    expect_text(by(page, "child-received").locator("li"), "again.txt:5")


def test_the_page_sends_no_leave_for_a_live_component_its_parent_hid(page, server):
    # #140: the parent's render already retired the child, and a leave arriving
    # after the child was shown again removed the new instance. Twice: the child
    # on the page at load and the one a render brought back register apart.
    sent: list[str] = []
    page.on("websocket", lambda ws: ws.on("framesent", lambda payload: sent.append(payload)))
    open_live(page, f"{server}/fileprobe/nested/")
    for _ in range(2):
        by(page, "toggle").click()
        expect_text(by(page, "shown"), "False")
        expect_count(by(page, "child-files"), 0)
        by(page, "toggle").click()
        expect_text(by(page, "shown"), "True")
        expect_count(by(page, "child-files"), 1)

    commands = [json.loads(frame)["command"] for frame in sent if isinstance(frame, str)]
    # Each showing toggle went out after anything the hiding morph before it sent
    assert commands.count("user_event") == 4
    assert "leave" not in commands


@pytest.fixture
def quick_reconnect(settings):
    """Every 100 ms, no jitter. Listed before ``page`` so the page is served with it."""
    settings.WIREVIEW = {
        **getattr(settings, "WIREVIEW", {}),
        "RECONNECT_MIN_DELAY_MS": 100,
        "RECONNECT_JITTER_MS": 0,
        "RECONNECT_MAX_DELAY_MS": 100,
        "RECONNECT_GROW_FACTOR": 1,
    }


PROBE_LIVE = "#probe[data-is-live='true']:not(.wireview-disconnected)"


def another_attempt_fails(page) -> None:
    """Wait until a reconnect started after this call has failed: the socket after it exists."""
    opened = page.evaluate("window.__link.sockets.length")
    page.wait_for_function(f"window.__link.sockets.length >= {opened + 2}", timeout=WAIT_TIMEOUT * 1000)


def test_a_file_chosen_while_offline_outlives_the_reconnects_that_fail(quick_reconnect, registered, page, server):
    # Every failed attempt closes the socket again. Each close used to end every
    # upload manager, the one holding the file chosen after the first among them.
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/fileprobe/", selector=PROBE_LIVE)
    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#probe")).to_have_class(re.compile("wireview-disconnected"))

    by(page, "files").set_input_files(text_file("offline.txt", "offline"))
    another_attempt_fails(page)
    page.evaluate("() => { window.__link.offline = false; }")

    wait_live(page, PROBE_LIVE)
    expect_text(by(page, "received").locator("li"), "offline.txt:7")
    assert registered == ["offline.txt"]


def test_a_file_chosen_before_the_first_connection_outlives_an_attempt_that_fails(
    quick_reconnect, registered, page, server
):
    # One script: two init scripts run in no promised order
    page.add_init_script(OFFLINE_SHIM + "window.__link.offline = true;")
    page.goto(f"{server}/fileprobe/")
    page.wait_for_function("window.__link.sockets.length >= 1", timeout=WAIT_TIMEOUT * 1000)

    # The server's first render has no file input yet -- the upload is allowed
    # in joined() -- but the drop zone is there
    drop(page, "early.txt", "early")
    another_attempt_fails(page)
    page.evaluate("() => { window.__link.offline = false; }")

    wait_live(page, PROBE_LIVE)
    expect_text(by(page, "received").locator("li"), "early.txt:5")
    assert registered == ["early.txt"]


def drop(page, name: str, body: str) -> None:
    """Drop one text file on the probe's drop zone."""
    page.evaluate(
        """([name, body]) => {
          const data = new DataTransfer();
          data.items.add(new File([body], name, { type: "text/plain" }));
          const zone = document.querySelector('[data-testid="drop"]');
          zone.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: data }));
          zone.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: data }));
        }""",
        [name, body],
    )


def test_the_drop_zone_uploads_what_is_dropped(probe):
    drop(probe, "d.txt", "dropped!")
    expect_text(by(probe, "received").locator("li"), "d.txt:8")


def test_the_preview_shows_the_chosen_image_before_it_is_uploaded(probe):
    by(probe, "images").set_input_files({"name": "p.png", "mimeType": "image/png", "buffer": PNG})
    expect(by(probe, "preview")).to_have_attribute("src", re.compile(r"^blob:"))


def test_an_external_upload_goes_to_the_presigned_url(probe):
    from testproj.fileprobe.views import RECEIVED

    RECEIVED.clear()
    by(probe, "outside").set_input_files(text_file("e.txt", "external"))
    expect_text(by(probe, "external-done"), "e.txt")
    assert list(RECEIVED.values()) == [b"external"]


def test_upload_events_carry_the_wireview_prefix_and_the_old_name_until_2_0(probe):
    """``upload:*`` was outside the public ``wireview:*`` events (#119)."""
    probe.evaluate(
        """() => {
          window.__seen = [];
          for (const name of ["wireview:upload-added", "wireview:upload-complete", "upload:complete"]) {
            document.addEventListener(name, (e) => window.__seen.push(name + ":" + e.detail.upload));
          }
        }"""
    )
    by(probe, "files").set_input_files(text_file("a.txt", "x"))
    expect_count(by(probe, "received").locator("li"), 1)

    seen = probe.evaluate("() => window.__seen")
    assert {"wireview:upload-added:files", "wireview:upload-complete:files", "upload:complete:files"} <= set(seen)


def test_a_component_a_live_render_draws_and_the_live_component_in_it_join_and_upload(page, server):
    # The box is an ordinary component a render brings in. Until the page joined
    # it, nothing ran its joined() -- where its uploads are set up -- nor settled
    # the LiveComponent inside it, whose joined() sets up its own; and no render
    # named either instance, which is what lets the page take their configs (#137).
    open_live(page, f"{server}/fileprobe/shelf/")
    by(page, "show").click()

    expect_text(by(page, "sprig-joins"), "1")
    by(page, "box-files").set_input_files(text_file("box.txt", "box"))
    expect_text(by(page, "box-received").locator("li"), "box.txt:3")
    by(page, "sprig-files").set_input_files(text_file("sprig.txt", "sprig"))
    expect_text(by(page, "sprig-received").locator("li"), "sprig.txt:5")


@pytest.fixture
def late_gate():
    """Holds the work ``FileLateShelf.joined()`` starts until the test opens it."""
    from testproj.fileprobe.live import FileLateShelf

    FileLateShelf.gate.clear()
    try:
        yield FileLateShelf.gate
    finally:
        FileLateShelf.gate.set()


def test_a_live_component_in_a_component_the_work_draws_comes_back_after_a_reconnect(page, server, late_gate):
    # The reconnect joins the shelf, whose render -- the work loading again --
    # has no box, and the box right behind it, as the page still has it. The
    # box's join took the entries the shelf's join carried for the box and the
    # sprig, and went when the page let the box go: once the work landed, the
    # sprig came back from its defaults.
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/fileprobe/late/")
    expect(page.locator("#box")).to_have_count(0)
    late_gate.set()
    expect_text(by(page, "sprig-joins"), "1")

    late_gate.clear()
    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#late-shelf")).to_have_class(re.compile("wireview-disconnected"))
    page.evaluate("() => { window.__link.offline = false; }")
    expect_text(by(page, "late-state"), "loading")
    expect(page.locator("#box")).to_have_count(0)
    late_gate.set()

    # Its own state comes back, and its joined() runs on the new connection
    expect_text(by(page, "sprig-joins"), "2")


def test_a_live_component_in_a_component_the_work_draws_comes_back_when_the_work_lands_first(page, server, late_gate):
    # As above, with the work landing before the page has patched the shelf's
    # first render in. The shelf's render after the work drew the box the page
    # had joined, and the page's leave for the box it let go came after it and
    # took that box, with the sprig the page let go with the old element. The
    # page joins the box the render drew without the sprig, and the sprig comes
    # back as it was: the box's join, and then this one, each ran its joined().
    page.add_init_script(OFFLINE_SHIM)
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/fileprobe/late/")
    expect(page.locator("#box")).to_have_count(0)
    late_gate.set()
    expect_text(by(page, "sprig-joins"), "1")

    late_gate.clear()
    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#late-shelf")).to_have_class(re.compile("wireview-disconnected"))
    page.evaluate("() => { window.__inbox.holding = true; window.__link.offline = false; }")
    # The answers to both joins, held: the box's comes last
    page.wait_for_function(
        "() => window.__inbox.held.some((m) => m.command === 'joined' && m.payload.id === 'box')",
        timeout=WAIT_TIMEOUT * 1000,
    )
    late_gate.set()
    # The shelf's render once the work landed, sent before the page let the box go
    page.wait_for_function(
        "() => window.__inbox.held.some((m) => m.command === 'render' && m.payload.id === 'late-shelf'"
        " && m.payload.vsn === undefined)",
        timeout=WAIT_TIMEOUT * 1000,
    )
    page.evaluate(
        "() => window.__inbox.release(window.__inbox.held.findIndex((m) => m.command === 'render'"
        " && m.payload.id === 'late-shelf' && m.payload.vsn === undefined))"
    )
    expect(page.locator("#box")).to_have_count(0)
    page.evaluate("() => window.__inbox.release()")

    expect_text(by(page, "sprig-joins"), "3")


#: Holds the page's join for ``box`` and everything it sends after it, until a test
#: says ``window.__outbox.release()``: the server handles one socket's messages in
#: order, and work that lands meanwhile runs before that join.
OUTBOX_SHIM = """
(() => {
  const Native = window.WebSocket;
  const outbox = { holding: false, held: [] };
  outbox.release = () => {
    outbox.holding = false;
    outbox.held.splice(0).forEach((send) => send());
  };
  window.__outbox = outbox;
  window.WebSocket = class extends Native {
    send(data) {
      const message = JSON.parse(data);
      const box = message.command === "join" && message.payload.state && message.payload.name === "FileBox";
      if (outbox.holding && (box || outbox.held.length)) {
        outbox.held.push(() => super.send(data));
        return;
      }
      super.send(data);
    }
  };
})();
"""


def test_a_live_component_in_a_component_the_work_drew_before_the_page_joined_it_comes_back(page, server, late_gate):
    # The work lands between the shelf's join and the box's, right behind it:
    # the shelf's render drew a new box, and the box's join took it up. The
    # page then let the box it had go, patching the shelf's first render in,
    # and that leave reached the server after the render that drew the new
    # box: it took the new box and the sprig in it. The page joined the box the
    # render drew, without the sprig, which it had let go with the old box.
    page.add_init_script(OFFLINE_SHIM)
    page.add_init_script(INBOX_SHIM)
    page.add_init_script(OUTBOX_SHIM)
    open_live(page, f"{server}/fileprobe/late/")
    expect(page.locator("#box")).to_have_count(0)
    late_gate.set()
    expect_text(by(page, "sprig-joins"), "1")

    late_gate.clear()
    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#late-shelf")).to_have_class(re.compile("wireview-disconnected"))
    page.evaluate(
        "() => { window.__inbox.holding = true; window.__outbox.holding = true; window.__link.offline = false; }"
    )
    # The shelf's join is answered; the box's waits on the page
    page.wait_for_function(
        "() => window.__inbox.held.some((m) => m.command === 'joined' && m.payload.id === 'late-shelf')",
        timeout=WAIT_TIMEOUT * 1000,
    )
    late_gate.set()
    page.wait_for_function(
        "() => window.__inbox.held.some((m) => m.command === 'render' && m.payload.id === 'late-shelf'"
        " && m.payload.vsn === undefined)",
        timeout=WAIT_TIMEOUT * 1000,
    )
    page.evaluate("() => window.__outbox.release()")
    page.wait_for_function(
        "() => window.__inbox.held.some((m) => m.command === 'joined' && m.payload.id === 'box')",
        timeout=WAIT_TIMEOUT * 1000,
    )
    # The page patches the shelf's first render in and lets the box go, then the rest
    page.evaluate(
        "() => window.__inbox.release(window.__inbox.held.findIndex((m) => m.command === 'render'"
        " && m.payload.id === 'late-shelf' && m.payload.vsn === undefined))"
    )
    expect(page.locator("#box")).to_have_count(0)
    page.evaluate("() => window.__inbox.release()")

    # The sprig's state came back: the new box's join, and then this one, each
    # ran its joined()
    expect_text(by(page, "sprig-joins"), "3")


def test_a_live_component_shown_again_after_a_reconnect_starts_anew(page, server):
    # The reconnect joins the shelf with every state under it, and then the box
    # with the sprig's: the shelf's pass had built the sprig already, so the box's
    # entry for it was never taken. It outlived the join, and the sprig the next
    # show built took it up -- a new instance with the old one's state.
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/fileprobe/shelf/")
    by(page, "show").click()
    expect_text(by(page, "sprig-joins"), "1")

    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#shelf")).to_have_class(re.compile("wireview-disconnected"))
    page.evaluate("() => { window.__link.offline = false; }")
    # Its own state comes back, and its joined() runs on the new connection
    expect_text(by(page, "sprig-joins"), "2")

    by(page, "hide").click()
    expect(page.locator("#box")).to_have_count(0)
    by(page, "show").click()
    expect_text(by(page, "sprig-joins"), "1")


# ---------------------------------------------------------------------------
# auto_upload=False: the form's submit uploads first (#264)
# ---------------------------------------------------------------------------

FORM_LIVE = "#upform[data-is-live='true']:not(.wireview-disconnected)"


@pytest.fixture
def form(page, server):
    open_live(page, f"{server}/fileprobe/form/", selector=FORM_LIVE)
    # The inputs come with the configs, which joined() allows
    expect(by(page, "photos")).to_have_count(1)
    return page


def entries(page, status: str):
    return by(page, "entries").locator(f"li[data-status='{status}']")


@pytest.fixture
def held_chunks(monkeypatch):
    """Hold every chunk at the endpoint until the test sets the returned event."""
    from wireview.views import UploadView

    release = threading.Event()
    arrived = threading.Event()
    post = UploadView.post

    async def held(self, request, *args, **kwargs):
        arrived.set()
        await asyncio.to_thread(release.wait, WAIT_TIMEOUT)
        return await post(self, request, *args, **kwargs)

    monkeypatch.setattr(UploadView, "post", held)
    release.arrived = arrived  # type: ignore[attr-defined]
    return release


@pytest.fixture
def user_events(monkeypatch):
    """The handlers the server ran for user events, in order."""
    from wireview.session import WireviewSession

    names: list[str] = []
    handle = WireviewSession.command_user_event

    async def recording(self, id, command, implicit_args, explicit_args, ref=None):
        names.append(command)
        return await handle(self, id, command, implicit_args, explicit_args, ref)

    monkeypatch.setattr(WireviewSession, "command_user_event", recording)
    return names


def test_a_manual_upload_goes_up_when_its_form_is_submitted(form):
    # The bug: the file stayed "pending" and save() found nothing to consume
    by(form, "photos").set_input_files(text_file("a.txt", "hello"))
    expect_count(entries(form, "pending"), 1)
    by(form, "title").fill("first")
    by(form, "save").click()

    expect(by(form, "saved").locator("li")).to_have_text(["title:first", "photos:a.txt:5"])
    expect_text(by(form, "saves"), "1")


def test_a_submit_with_nothing_to_upload_goes_at_once(form):
    by(form, "title").fill("plain")
    by(form, "save").click()
    expect(by(form, "saved").locator("li")).to_have_text(["title:plain"])


def test_every_input_and_the_drop_zone_of_the_form_go_up_before_one_submit(form):
    from testproj.fileprobe.views import RECEIVED

    RECEIVED.clear()
    by(form, "photos").set_input_files(text_file("p.txt", "photo"))
    drop(form, "d.txt", "dropped")
    by(form, "notes").set_input_files(text_file("n.txt", "note"))
    by(form, "remote").set_input_files(text_file("r.txt", "remote"))
    expect_count(entries(form, "pending"), 4)
    by(form, "save").click()

    expect(by(form, "saved").locator("li")).to_have_text(
        ["title:", "photos:p.txt:5", "photos:d.txt:7", "notes:n.txt:4", "remote:r.txt"]
    )
    expect_text(by(form, "saves"), "1")
    assert sorted(RECEIVED.values()) == [b"remote"]


def test_a_live_components_form_uploads_its_own_files(form):
    by(form, "child-files").set_input_files(text_file("c.txt", "child"))
    by(form, "child-save").click()
    expect_text(by(form, "child-saved").locator("li"), "c.txt:5")
    expect_text(by(form, "saves"), "0")


def test_the_submit_stays_disabled_while_its_uploads_go_and_a_second_submit_is_ignored(held_chunks, user_events, form):
    by(form, "photos").set_input_files(text_file("slow.txt", "slow"))
    expect_count(entries(form, "pending"), 1)
    by(form, "save").click()
    held_chunks.arrived.wait(WAIT_TIMEOUT)

    expect(by(form, "save")).to_be_disabled()
    expect(by(form, "save")).to_have_text("Saving...")
    expect(by(form, "form")).to_have_class(re.compile("wireview-submit-loading"))
    form.evaluate("() => document.querySelector('[data-testid=form]').requestSubmit()")

    held_chunks.set()
    expect(by(form, "saved").locator("li")).to_have_text(["title:", "photos:slow.txt:4"])
    expect(by(form, "save")).to_be_enabled()
    expect(by(form, "save")).to_have_text("Save")
    expect_text(by(form, "saves"), "1")
    assert user_events == ["save"]


def test_a_failed_upload_keeps_the_submit_back_and_the_next_submit_sends_what_is_left(user_events, form):
    # The server refuses a .png that is not one when its last chunk lands
    by(form, "photos").set_input_files({"name": "fake.png", "mimeType": "image/png", "buffer": b"not a png"})
    by(form, "notes").set_input_files(text_file("n.txt", "note"))
    expect_count(entries(form, "pending"), 2)
    by(form, "save").click()

    expect_text(entries(form, "error"), "photos:fake.png:error:File content doesn't match file type")
    expect(by(form, "save")).to_be_enabled()
    expect(by(form, "form")).not_to_have_class(re.compile("wireview-loading"))
    expect_count(entries(form, "pending"), 0)
    assert user_events == []

    by(form, "save").click()
    expect(by(form, "saved").locator("li")).to_have_text(["title:", "notes:n.txt:4"])
    assert user_events == ["save"]


def test_a_cancelled_upload_keeps_the_submit_back(held_chunks, user_events, form):
    by(form, "photos").set_input_files(text_file("gone.txt", "gone"))
    expect_count(entries(form, "pending"), 1)
    by(form, "save").click()
    held_chunks.arrived.wait(WAIT_TIMEOUT)

    form.evaluate(
        """() => {
          const el = document.getElementById("upform");
          const [entry] = window.wireview.getUploadManager(el).getEntries("photos");
          window.wireview.cancelUpload(el, "photos", entry.ref);
        }"""
    )
    expect(by(form, "save")).to_be_enabled()
    held_chunks.set()
    expect_count(entries(form, "cancelled"), 1)
    assert user_events == []


def test_a_submit_whose_uploads_the_connection_lost_is_not_sent_after_the_reconnect(
    quick_reconnect, held_chunks, user_events, page, server
):
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/fileprobe/form/", selector=FORM_LIVE)
    by(page, "photos").set_input_files(text_file("lost.txt", "lost"))
    expect_count(entries(page, "pending"), 1)
    by(page, "save").click()
    held_chunks.arrived.wait(WAIT_TIMEOUT)

    page.evaluate("() => window.__link.sockets.forEach((s) => s.close())")
    expect(page.locator("#upform")).to_have_class(re.compile("wireview-disconnected"))
    expect(by(page, "save")).to_be_enabled()
    held_chunks.set()

    # The new connection's instance has no entries: the file is chosen again
    wait_live(page, FORM_LIVE)
    expect_count(entries(page, "pending"), 0)
    assert user_events == []
    by(page, "photos").set_input_files(text_file("again.txt", "again"))
    expect_count(entries(page, "pending"), 1)
    by(page, "save").click()
    expect(by(page, "saved").locator("li")).to_have_text(["title:", "photos:again.txt:5"])
    assert user_events == ["save"]


def test_a_manual_input_outside_any_form_says_once_that_nothing_starts_it(form):
    warnings: list[str] = []
    form.on("console", lambda message: message.type == "warning" and warnings.append(message.text))
    by(form, "stray").set_input_files(text_file("s1.txt", "one"))
    expect_count(entries(form, "pending"), 1)
    by(form, "stray").set_input_files(text_file("s2.txt", "two"))
    expect_count(entries(form, "pending"), 2)
    stray = [text for text in warnings if "stray" in text]
    assert len(stray) == 1, warnings
    assert "auto_upload=False" in stray[0]
