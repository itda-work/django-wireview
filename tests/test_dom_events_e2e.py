"""A browser hears what the test helpers say it hears, and holds the stream items they hold (GAP-056, #252).

Each action in ``testproj.domprobe.scenarios`` is done here by a user in a
browser, on the page that draws the probe's components together, and the
handlers must hear what ``tests/test_dom_events.py`` holds the helpers to:
delegation, .stop, a modifier that does not hold, a debounce, a JS() chain,
a checkbox and a radio, a form's fields and named buttons, a nested component
and a LiveComponent.

The stream test sends the page's client a random run of stream operations, as
the server's messages, and compares each container's items with what the
helpers' copy (``testing_dom.apply_stream_ops``) makes of the same run.
"""

import json
import random
import re

import pytest
from playwright.sync_api import expect
from testproj.domprobe.live import HEARD, DomHooked
from testproj.domprobe.scenarios import HOOK_HEARD, HOOK_REPLY, SCENARIOS, by, heard
from testproj.e2e_browser import INBOX_SHIM, OFFLINE_SHIM, WAIT_TIMEOUT, expect_text, open_live
from testproj.e2e_server import serve

from wireview.testing_dom import Selector, apply_stream_ops, element_children, parse

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_a_browser_hears_what_the_helpers_send(page, server, scenario):
    open_live(page, f"{server}/domprobe/")
    HEARD.clear()
    scenario.browser(page)
    expect_text(by(page, scenario.output), scenario.done)  # type: ignore[arg-type]
    assert heard(scenario.component, scenario.expected, HEARD) == scenario.expected


# What stream items can be: a plain one, one with an id inside it (an insert of
# that id finds it), leading space, a table row (a body's parser drops the row
# and keeps its cells' contents), none at all, two elements (the first goes)
ITEM_HTML = [
    '<li class="a">plain</li>',
    '<li><span id="s-inner">holds s-inner</span></li>',
    "  <li>leading space</li>",
    "<tr><td><b>cell</b></td></tr>",
    "",
    "only text",
    "<p>first</p><p>second</p>",
]
ITEM_IDS = ["s-1", "s-2", "s-3", "s-4", "s-inner", "seed-host"]
# (owner, stream): the host's own rows, the guest's own rows, the guest's log
# (the host has none: it takes the first inside it), no owner, no such container
TARGETS = [("host", "rows"), ("guest", "rows"), ("host", "log"), ("guest", "log"), (None, "rows"), ("guest", "nope")]


def _random_ops(rng: random.Random, count: int) -> list[dict]:
    ops = []
    for _ in range(count):
        owner, stream = rng.choice(TARGETS)
        kind = rng.choice(["insert", "insert", "insert", "delete", "reset"])
        items = [
            {"id": rng.choice(ITEM_IDS), "html": rng.choice(ITEM_HTML)} for _ in range(rng.choice([0, 1, 1, 2, 3]))
        ]
        op = {
            "op": kind,
            "stream": stream,
            "items": items,
            "at": rng.choice([-1, -1, 0, 1, 3]),
            "limit": rng.choice([0, 0, 2, 3, -1]),
        }
        if owner is not None:
            op["id"] = owner
        if kind == "insert" and rng.random() < 0.2:
            op["update_only"] = True
        ops.append(op)
    return ops


def _summary(container) -> list[list[str]]:
    return [[el.tag, el.get("id") or "", " ".join(el.text.split())] for el in element_children(container)]


READ_CONTAINERS = """async () => {
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  return [...document.querySelectorAll("[wire-stream]")].map((c) =>
    [...c.children].map((el) => [
      el.tagName.toLowerCase(),
      el.id,
      el.textContent.split(/\\s+/).filter(Boolean).join(" "),
    ])
  );
}"""

JOINED = """async () => {
  if (window.__inbox.seen.filter((m) => m.command === "joined").length < 2) return false;
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  return true;
}"""

SEND = """(messages) => {
  const socket = window.__link.sockets.at(-1);
  for (const data of messages) socket.dispatchEvent(new MessageEvent("message", { data }));
}"""


@pytest.mark.parametrize("seed", range(6))
def test_a_page_holds_the_stream_items_the_helpers_hold(page, server, seed):
    page.add_init_script(OFFLINE_SHIM)
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/domprobe/streams/")
    # Both joins answered and their renders patched in: a join's render that came after the operations
    # would be a run of its own, and the containers are what the operations alone left
    page.wait_for_function(JOINED, timeout=WAIT_TIMEOUT * 1000)
    root = parse(page.content())
    ops = _random_ops(random.Random(seed), 12)
    page.evaluate(SEND, [json.dumps({"command": "stream_op", "payload": op}) for op in ops])
    theirs = page.evaluate(READ_CONTAINERS)
    apply_stream_ops(root, [{"type": "stream_op", **op} for op in ops])
    ours = [_summary(c) for c in Selector("[wire-stream]").select(root)]
    assert ours == theirs, f"the helpers and the client part on {ops}"


def test_a_hooks_push_is_what_hook_event_sends(page, server):
    """The hook pushes on a click; the helper pushes the same event: handle_hook_event hears the same, and the
    reply is what the hook's Promise got. ``tests/test_dom_events.py`` holds the helper to the same values."""
    open_live(page, f"{server}/domprobe/")
    HEARD.clear()
    by(page, "chart").click()
    expect(page.locator("html")).to_have_attribute("data-dom-reply", re.compile("point"))
    assert json.loads(page.locator("html").get_attribute("data-dom-reply") or "") == HOOK_REPLY
    assert heard(DomHooked, HOOK_HEARD, HEARD) == HOOK_HEARD
