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
from testproj.domprobe.scenarios import (
    FAMILY,
    FAMILY_JOINED,
    HOOK_HEARD,
    HOOK_REPLY,
    HTTP_SUBMITS,
    PAYLOADS,
    SCENARIOS,
    by,
    family_heard,
    heard,
    submitted,
)
from testproj.domprobe.views import RECEIVED
from testproj.e2e_browser import INBOX_SHIM, OFFLINE_SHIM, WAIT_TIMEOUT, expect_text, open_live, wait_joined
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


#: An init script that keeps every user_event the page sends, as sent
FRAMES_SHIM = """
(() => {
  const send = WebSocket.prototype.send;
  window.__events = [];
  WebSocket.prototype.send = function (data) {
    try {
      const message = JSON.parse(data);
      if (message.command === "user_event") window.__events.push(message.payload);
    } catch (e) {}
    return send.call(this, data);
  };
})();
"""

#: What the browser itself would submit: FormData(form, submitter), each name with its values in order
FORM_DATA = """([form, submitter]) => {
  const data = new FormData(document.querySelector(`[data-testid=${form}]`),
                            submitter ? document.querySelector(`[data-testid=${submitter}]`) : null);
  const sent = {};
  for (const [name, value] of data) (sent[name] ??= []).push(typeof value === "string" ? value : "<file>");
  return sent;
}"""


@pytest.mark.parametrize("payload", PAYLOADS, ids=[p.name for p in PAYLOADS])
def test_a_submit_sends_what_the_browsers_own_submission_would(page, server, payload):
    """The client's fields for a submit are FormData's: no disabled control, the submitter alone of the
    buttons, the controls ``form=`` gives the form (#244). The helpers are held to the same table."""
    page.add_init_script(FRAMES_SHIM)
    open_live(page, f"{server}/domprobe/payload/")

    assert page.evaluate(FORM_DATA, [payload.form, payload.submitter]) == payload.sent

    page.evaluate(
        """([form, submitter]) => document.querySelector(`[data-testid=${form}]`)
             .requestSubmit(submitter ? document.querySelector(`[data-testid=${submitter}]`) : undefined)""",
        [payload.form, payload.submitter],
    )
    page.wait_for_function("window.__events.length > 0", timeout=WAIT_TIMEOUT * 1000)
    (event,) = page.evaluate("window.__events")
    assert submitted(event["implicit_args"]) == payload.sent


SHOWN = """() => {
  const form = document.querySelector("[data-testid=form]");
  return {
    agree: form.querySelector("[data-testid=agree]").checked,
    tags: [...form.querySelector("[data-testid=tags]").selectedOptions].map((o) => o.value),
    size: form.querySelector("[data-testid=size]").value,
    free: form.querySelector("[data-testid=plan-free]").checked,
    pro: form.querySelector("[data-testid=plan-pro]").checked,
    x: form.querySelector("[data-testid=opt-x]").checked,
  };
}"""

PICKED = {"agree": True, "tags": ["b"], "size": "s", "free": False, "pro": True, "x": False}


def test_the_answer_to_an_older_change_leaves_newer_picks_alone(page, server):
    """A render answering a change sent before the user's newer picks does not undo them (#271).

    The tag run of 1.6.0 failed ``[submit-values]`` this way on a slow runner:
    each pick sends a change, and an answer that lands after the next pick
    carries the form as it was before it. The radio is the hard case: the
    morph checks the server's radio first, which unchecks the user's.
    """
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/domprobe/")
    wait_joined(page)
    renders = "window.__inbox.seen.concat(window.__inbox.held).filter((m) => m.command === 'render').length"
    before = page.evaluate(renders)
    page.evaluate("window.__inbox.holding = true")

    by(page, "agree").check()
    page.wait_for_function("window.__inbox.held.some((m) => m.command === 'render')", timeout=WAIT_TIMEOUT * 1000)
    by(page, "tags").select_option(["b"])
    by(page, "size").select_option("s")
    by(page, "plan-pro").check()
    by(page, "opt-x").uncheck()

    # Only the answer to the tick: the server's form from before the other picks
    page.evaluate("window.__inbox.release(window.__inbox.held.findIndex((m) => m.command === 'render') + 1)")
    assert page.evaluate(SHOWN) == PICKED

    # Every answer: the last one is the server's form after every pick
    page.evaluate("window.__inbox.release()")
    page.wait_for_function(f"{renders} >= {before + 5}", timeout=WAIT_TIMEOUT * 1000)
    assert page.evaluate(SHOWN) == PICKED


# What stream items can be: a plain one, one with an id inside it (an insert of
# that id finds it), leading space, a table row and a cell (kept whole, also
# in a list's container), none at all, two elements (the first goes)
ITEM_HTML = [
    '<li class="a">plain</li>',
    '<li><span id="s-inner">holds s-inner</span></li>',
    "  <li>leading space</li>",
    "<tr><td><b>cell</b></td></tr>",
    "<td>a cell</td>",
    "",
    "only text",
    "<p>first</p><p>second</p>",
]
ITEM_IDS = ["s-1", "s-2", "s-3", "s-4", "s-inner", "seed-host"]
# (owner, stream): the host's own rows, the guest's own rows, the guest's log
# (the host has none: it takes the first inside it), no owner, no such container
TARGETS = [
    ("host", "rows"),
    ("guest", "rows"),
    ("host", "log"),
    ("guest", "log"),
    ("guest", "cells"),
    (None, "rows"),
    ("guest", "nope"),
]


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

# One message a frame. A stream op whose container is not on the page holds its component's later ops
# for a frame (TargetQueue) while another component's apply at once; the helpers drop it at once, so a
# batch of messages in one frame may land in another order than theirs
SEND = """async (messages) => {
  const socket = window.__link.sockets.at(-1);
  for (const data of messages) {
    socket.dispatchEvent(new MessageEvent("message", { data }));
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  }
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


def test_a_table_row_streamed_into_a_tbody_is_a_row(page, server):
    """A ``<tr>`` item goes into its ``<tbody wire-stream>`` as a row, and its button is the helpers' to press too."""
    open_live(page, f"{server}/domprobe/table/")
    rows = page.locator("[data-testid=people] > tr")
    expect(rows).to_have_count(2)
    assert [rows.nth(i).get_attribute("id") for i in range(2)] == ["person-1", "person-2"]
    expect(rows.nth(1)).to_contain_text("Grace")
    HEARD.clear()
    page.locator("#person-1 button").click()
    expect(rows).to_have_count(1)
    assert [(name, kwargs) for _, name, kwargs in HEARD] == [("remove", {"id": 1})]


@pytest.mark.parametrize("case", HTTP_SUBMITS, ids=[c.name for c in HTTP_SUBMITS])
def test_a_forms_own_submission_sends_what_submit_form_sends(page, server, case):
    """The browser submits the form without script; ``tests/test_submit_form.py`` holds ``submit_form()`` to
    the same table (#252)."""
    RECEIVED.clear()
    open_live(page, f"{server}/domprobe/http/")
    with page.expect_navigation(timeout=WAIT_TIMEOUT * 1000) as navigation:
        page.click(f"[data-testid={case.submitter}]")
    assert navigation.value is not None and navigation.value.status == 200
    (got,) = RECEIVED
    assert got["method"] == case.method
    assert got["fields"] == case.fields


def test_a_form_without_its_token_is_refused_in_the_browser_too(page, server):
    RECEIVED.clear()
    open_live(page, f"{server}/domprobe/http/")
    with page.expect_navigation(timeout=WAIT_TIMEOUT * 1000) as navigation:
        page.click("[data-testid=bare-go]")
    assert navigation.value is not None and navigation.value.status == 403
    assert RECEIVED == []


@pytest.mark.parametrize("step", range(len(FAMILY)), ids=[s.name for s in FAMILY])
def test_a_held_page_hears_in_the_browser_what_the_helpers_run(page, server, step):
    """``FAMILY`` up to ``step``, by a user: the nested component, the LiveComponent and their parent hear
    what ``tests/test_mounted_page.py`` holds the helpers to, and the page shows the same."""
    HEARD.clear()
    open_live(page, f"{server}/domprobe/family/")
    expect_text(by(page, "pet-text"), "a:0")
    assert sorted(family_heard(HEARD)) == sorted(FAMILY_JOINED)
    for done in FAMILY[: step + 1]:
        HEARD.clear()
        page.click(f"[data-testid={done.testid}]")
        for testid, text in done.shows.items():
            if text is None:
                expect(page.locator(f"[data-testid={testid}]")).to_have_count(0, timeout=WAIT_TIMEOUT * 1000)
            else:
                expect_text(by(page, testid), text)
        assert family_heard(HEARD) == done.heard, done.name
