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
from testproj.e2e_browser import OFFLINE_SHIM, WAIT_TIMEOUT, expect_count, expect_text, open_live, wait_live
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
    # after the child was shown again removed the new instance.
    sent: list[str] = []
    page.on("websocket", lambda ws: ws.on("framesent", lambda payload: sent.append(payload)))
    open_live(page, f"{server}/fileprobe/nested/")
    by(page, "toggle").click()
    expect_text(by(page, "shown"), "False")
    expect_count(by(page, "child-files"), 0)
    by(page, "toggle").click()
    expect_text(by(page, "shown"), "True")

    commands = [json.loads(frame)["command"] for frame in sent if isinstance(frame, str)]
    # The second toggle went out after anything the hiding morph sent
    assert commands.count("user_event") == 2
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
