"""Uploads, every way in, in a real browser (#110).

The drop zone, the preview and external uploads had no test; the file input was
only ever used for one file. Fixture: tests/testproj/fileprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_count, expect_text, open_live
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
