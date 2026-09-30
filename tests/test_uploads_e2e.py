"""Uploads, every way in, in a real browser (#110).

The drop zone, the preview and external uploads had no test; the file input was
only ever used for one file. Fixture: tests/testproj/fileprobe/.
"""

import asyncio
import re
import threading

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import WAIT_TIMEOUT, expect_count, expect_text, open_live, wait_live
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


def test_the_drop_zone_uploads_what_is_dropped(probe):
    probe.evaluate(
        """() => {
          const data = new DataTransfer();
          data.items.add(new File(["dropped!"], "d.txt", { type: "text/plain" }));
          const zone = document.querySelector('[data-testid="drop"]');
          zone.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: data }));
          zone.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: data }));
        }"""
    )
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
