"""The test helpers that read the page's bindings: click(), change(), input(), submit() (GAP-056, #252).

``view.call("remove", id=3)`` passes whether or not any button on the page calls
``remove``. These helpers find the element, dispatch the event at it and send
what the client would -- so the binding is what is tested.

Three kinds of check:

- the client's binding rules (``events.mjs``) and the helpers' copy agree on
  every input: ``tests/js/bindings.mjs`` runs the client's functions on the
  same random cases;
- the actions on ``testproj.domprobe`` hear what ``SCENARIOS`` says, which
  ``tests/test_dom_events_e2e.py`` checks a browser hears too;
- each way a test can ask for what no user can do fails, saying why.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from testproj.domprobe.live import HEARD, DomBubble, DomChat, DomForm, DomKeys, DomNest, DomTrap
from testproj.domprobe.scenarios import SCENARIOS, heard

from wireview import JS, Component, mount
from wireview.event_transpiler import MODIFIER_ARGUMENTS, MODIFIERS
from wireview.testing_dom import (
    NON_BUBBLING,
    Element,
    Selector,
    _Dispatch,
    key_event,
    parse,
    plan_insert,
    plan_trim,
)

DRIVER = Path(__file__).parent / "js" / "bindings.mjs"


@pytest.fixture(autouse=True)
def _clear_heard():
    HEARD.clear()
    yield
    HEARD.clear()


# ============================================================================
# The client's rules, on the same inputs
# ============================================================================

EVENT_TYPES = ["click", "change", "input", "submit", "keyup", "keydown", "focus", "blur", "focusin", "focusout"]
TOKENS = [*MODIFIERS, "Enter", "ESC", "away", ""]


def _binding_name(rng: random.Random, type_: str) -> str:
    """A binding attribute, valid or not: the client reads whatever a page carries. Half are for ``type_``."""
    tokens = [type_ if rng.random() < 0.5 else rng.choice(EVENT_TYPES)]
    for _ in range(rng.choice([0, 1, 1, 2, 3, 4])):
        token = rng.choice(TOKENS)
        tokens.append(token)
        if token in MODIFIER_ARGUMENTS and rng.random() < 0.9:
            tokens.append(rng.choice(["0", "30", "300", "enter", "a", "13", "65", "x1"]))
    return "wire-on-" + ".".join(tokens)


def _event(rng: random.Random) -> dict:
    if rng.random() < 0.3:
        # What the helpers dispatch for an event without keys
        return {}
    if rng.random() < 0.5:
        # What keydown() and keyup() dispatch
        return key_event(
            rng.choice(["Enter", "a", "Z", "7", "Escape", " ", "ArrowUp", "Tab", "Delete", "Backspace", "한"]),
            ctrl=rng.random() < 0.3,
            alt=rng.random() < 0.2,
            shift=rng.random() < 0.2,
            meta=rng.random() < 0.2,
            composing=rng.random() < 0.15,
        )
    return {
        "key": rng.choice(["Enter", "a", "Escape", " ", "ArrowUp"]),
        "keyCode": rng.choice([13, 65, 27]),
        "isComposing": rng.random() < 0.2,
        "ctrlKey": rng.random() < 0.5,
        "altKey": rng.random() < 0.3,
        "shiftKey": rng.random() < 0.3,
        "metaKey": rng.random() < 0.3,
    }


def _case(rng: random.Random) -> dict:
    type_ = rng.choice(EVENT_TYPES)
    chain = [
        [_binding_name(rng, type_) for _ in range(rng.choice([0, 0, 1, 1, 2, 3]))]
        for _ in range(rng.choice([1, 2, 3, 4]))
    ]
    # An element carries an attribute once
    chain = [list(dict.fromkeys(names)) for names in chain]
    return {"chain": chain, "type": type_, "event": _event(rng), "bubbles": type_ not in NON_BUBBLING}


class _Recorder:
    """A page whose every binding is recorded where it fired, in place of being sent."""

    def __init__(self, elements: list[Element]) -> None:
        self.elements = elements
        self.fired: list[list] = []

    def server_event(self, el, handler, args, target, type_):
        self.fired.append([self.elements.index(el), handler])
        return None


def _helpers_dispatch(case: dict) -> dict:
    # The chain as elements, target first; each binding names itself as its handler
    elements: list[Element] = []
    parent = None
    for names in reversed(case["chain"]):
        el = Element("div", [(name, json.dumps({"h": name})) for name in names], parent)
        if parent is not None:
            parent.children.append(el)
        parent = el
        elements.insert(0, el)
    recorder = _Recorder(elements)
    action = _Dispatch(recorder)  # type: ignore[arg-type]
    prevented = action.dispatch(elements[0], case["type"], case["event"])
    action.run_timers()
    return {"fired": recorder.fired, "prevented": prevented}


@pytest.mark.integration
def test_the_helpers_run_bindings_as_the_client_does():
    rng = random.Random(252)
    cases = [_case(rng) for _ in range(3000)]
    node = shutil.which("node")
    # Not a skip: this is what holds the helpers to the client, and node builds the bundle
    assert node, "node is required: tests/js/bindings.mjs runs events.mjs"
    out = subprocess.run(
        [node, str(DRIVER)], input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", check=True
    )
    client = json.loads(out.stdout)
    fired = 0
    for case, theirs in zip(cases, client, strict=True):
        ours = _helpers_dispatch(case)
        assert ours == theirs, f"the helpers and events.mjs part on {case}"
        fired += len(ours["fired"])
    # The cases exercise firing, not only skipping
    assert fired > 400


STREAMS = Path(__file__).parent.parent / "wireview" / "static" / "wireview" / "streams.mjs"


@pytest.mark.integration
def test_the_helpers_place_stream_items_as_streams_mjs_does():
    inserts = [
        {"exists": e, "at": at, "childCount": n, "updateOnly": u, "placed": placed}
        for e in (False, True)
        for at in (-1, 0, 1, 2, 5)
        for n in (0, 1, 2, 4)
        for u in (False, True)
        for placed in (0, 1, 3)
    ]
    trims = [
        {"childCount": n, "limit": limit, "at": at} for n in range(5) for limit in (-1, 0, 1, 3) for at in (-1, 0, 2)
    ]
    script = (
        f"import {{ planInsert, planTrim }} from {json.dumps(STREAMS.as_uri())};"
        "let input = ''; for await (const c of process.stdin) input += c;"
        "const { inserts, trims } = JSON.parse(input);"
        "process.stdout.write(JSON.stringify({ inserts: inserts.map(planInsert), trims: trims.map(planTrim) }));"
    )
    node = shutil.which("node")
    assert node, "node is required: the stream plans are compared with streams.mjs"
    out = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps({"inserts": inserts, "trims": trims}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    client = json.loads(out.stdout)
    ours = [
        plan_insert(
            exists=c["exists"], at=c["at"], child_count=c["childCount"], update_only=c["updateOnly"], placed=c["placed"]
        )
        for c in inserts
    ]
    assert ours == client["inserts"]
    assert [plan_trim(child_count=c["childCount"], limit=c["limit"], at=c["at"]) for c in trims] == client["trims"]


# ============================================================================
# The probe: the same actions a browser does (tests/test_dom_events_e2e.py)
# ============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
async def test_an_action_sends_what_the_page_would(scenario):
    view = await mount(scenario.component)
    await scenario.helper(view)
    assert heard(scenario.component, scenario.expected, HEARD) == scenario.expected


@pytest.mark.integration
@pytest.mark.asyncio
async def test_it_returns_the_render_after_the_events():
    view = await mount(DomBubble)
    html = await view.click("[data-testid=bare]")
    assert html == view.render()
    assert '<output data-testid="heard-bubble">wrapped</output>' in html


@pytest.mark.integration
@pytest.mark.asyncio
async def test_text_picks_among_the_matches():
    view = await mount(DomBubble)
    await view.click("section button", text="stop")
    assert [name for _, name, _ in HEARD] == ["inner"]
    HEARD.clear()
    await view.click("button", text=re.compile(r"^in"))
    assert [name for _, name, _ in HEARD] == ["inner", "wrapped"]


# ============================================================================
# The mistake they are for
# ============================================================================


class DomTrapUnbound(DomTrap):
    class Meta:
        template_name = "domprobe/trap_unbound.html"


class DomTrapMiswired(DomTrap):
    class Meta:
        template_name = "domprobe/trap_miswired.html"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_button_wired_to_the_handler_passes_both():
    view = await mount(DomTrap)
    await view.click("[data-testid=remove]")
    await view.call("remove", id=3)
    assert [(name, kwargs) for _, name, kwargs in HEARD] == [("remove", {"id": 3}), ("remove", {"id": 3})]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_deleted_binding_fails_the_click_and_not_the_call():
    view = await mount(DomTrapUnbound)
    await view.call("remove", id=3)
    with pytest.raises(AssertionError, match=r"reaches no handler"):
        await view.click("[data-testid=remove]")
    assert [name for _, name, _ in HEARD] == ["remove"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_miswired_button_calls_what_it_is_wired_to():
    view = await mount(DomTrapMiswired)
    await view.call("remove", id=3)
    await view.click("[data-testid=remove]")
    # The click ran archive: a test that asserts remove's effect fails, as the page does
    assert [name for _, name, _ in HEARD] == ["remove", "archive"]


class DomChatUnbound(DomChat):
    class Meta:
        template_name = "domprobe/chat_unbound.html"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_deleted_key_binding_fails_the_key_and_not_the_call():
    view = await mount(DomChat)
    await view.keyup("[data-testid=draft]", "Enter")
    unbound = await mount(DomChatUnbound)
    await unbound.call("send", draft="hi")
    with pytest.raises(AssertionError, match=r"The keyup on <input .*reaches no handler: it dispatched keyup"):
        await unbound.keyup("[data-testid=draft]", "Enter")
    assert [name for _, name, _ in HEARD] == ["send", "send"]


# ============================================================================
# What fails, and says why
# ============================================================================


async def _fails(view, action, match: str) -> None:
    with pytest.raises(AssertionError, match=match):
        await action(view)
    assert HEARD == [], "a failed action sends nothing"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("component", "action", "match"),
    [
        (DomBubble, lambda v: v.click("[data-testid=nope]"), r"No element matches '\[data-testid=nope\]'"),
        (DomBubble, lambda v: v.click("button"), r"6 elements match 'button'.*\n  <button"),
        (DomBubble, lambda v: v.click("button", text="nothing"), r"No element matches 'button' with text 'nothing'"),
        # Nothing on the way up from the output binds a click
        (DomBubble, lambda v: v.click("output"), r"reaches no handler"),
        (DomForm, lambda v: v.click("[data-testid=plan-free]"), r"reaches no handler"),
        # A disabled field is not the user's
        (DomForm, lambda v: v.change("input[name=locked]", "x"), r"it is disabled"),
        (DomForm, lambda v: v.change("input[name=secret]", "x"), r"hidden input"),
        (DomForm, lambda v: v.change("[data-testid=size]", "xl"), r"no option 'xl'"),
        (DomForm, lambda v: v.change("[data-testid=tags]", "a"), r"give the values to choose as a list"),
        (DomForm, lambda v: v.change("[data-testid=agree]", "yes"), r"give True to check it"),
        (DomForm, lambda v: v.change("[data-testid=title]", 3), r"give the value as a str"),
        (DomForm, lambda v: v.change("[data-testid=save]", "x"), r"is not a field"),
        (DomForm, lambda v: v.submit("form", values={"nope": "x"}), r"has no field called 'nope'"),
        (DomForm, lambda v: v.submit("form", values={"locked": "x"}), r"it is disabled"),
        (DomForm, lambda v: v.submit("form", values={"plan": "gold"}), r"No radio button 'plan'.*'gold'"),
        (DomForm, lambda v: v.submit("form", submitter="[data-testid=title]"), r"is not a submit button"),
        (DomForm, lambda v: v.submit("[data-testid=title]"), r"is not a form"),
        # A key the binding does not take, and one an IME is composing with
        (DomChat, lambda v: v.keyup("[data-testid=draft]", "Escape"), r"its key 'enter' modifier does not hold"),
        (
            DomChat,
            lambda v: v.keyup("[data-testid=draft]", "Enter", composing=True),
            r"its key 'enter' modifier does not hold",
        ),
        (DomChat, lambda v: v.keydown("[data-testid=draft]", "Enter"), r"reaches no handler: it dispatched keydown"),
        # A modifier key not held
        (DomKeys, lambda v: v.keydown("[data-testid=q2]", "a", shift=True), r"reaches no handler"),
        # What cannot take focus
        (DomKeys, lambda v: v.focus("output"), r"Cannot focus <output.*cannot take focus"),
        (DomKeys, lambda v: v.blur("[data-testid=search]"), r"Cannot blur <form"),
        (DomForm, lambda v: v.focus("input[name=locked]"), r"Cannot focus .*: it is disabled"),
        # The binding belongs to a nested component, which mount() does not run
        (DomNest, lambda v: v.click("[data-testid=leaf-button]"), r"calls 'leaf' on DomLeaf 'leaf'.*Mount DomLeaf"),
        (DomNest, lambda v: v.click("[data-testid=live-button]"), r"calls 'bump' on DomLive 'live'"),
    ],
)
async def test_what_no_user_can_do_fails(component, action, match):
    view = await mount(component)
    await _fails(view, action, match)


class DomDisabled(Component):
    class Meta:
        template_name = "domprobe/disabled.html"

    @property
    def push_chain(self) -> JS:
        return JS().push("pushed")

    async def hit(self, **kwargs) -> None:
        HEARD.append(("DomDisabled", "hit", kwargs))

    async def pushed(self) -> None:
        HEARD.append(("DomDisabled", "pushed", {}))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selector", "match"),
    [
        ("[data-testid=off]", r"Cannot click .*: it is disabled"),
        ("[data-testid=in-off]", r"inside the disabled <button"),
        ("[data-testid=in-fieldset]", r"inside the disabled <fieldset"),
        ("[data-testid=pusher]", r"JS\(\) chain .* pushes 'pushed'"),
        ("[data-testid=unprevented]", r"Nothing prevents the submit"),
        ("[data-testid=plain-submit]", r"Nothing prevents the submit"),
        ("[data-testid=no-component]", r"is in no component's element"),
    ],
)
async def test_a_click_no_user_can_make_fails(selector, match):
    view = await mount(DomDisabled)
    await _fails(view, lambda v: v.click(selector), match)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_legend_of_a_disabled_fieldset_is_not_disabled():
    view = await mount(DomDisabled)
    await view.click("[data-testid=in-legend]")
    assert [name for _, name, _ in HEARD] == ["hit"]


class DomLabels(Component):
    class Meta:
        template_name = "domprobe/labels.html"

    async def changed(self, **kwargs) -> None:
        HEARD.append(("DomLabels", "changed", kwargs))

    async def pressed(self, **kwargs) -> None:
        HEARD.append(("DomLabels", "pressed", {}))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_label_whose_for_names_no_field_clicks_nothing():
    view = await mount(DomLabels)
    await view.call("changed", a="on")
    HEARD.clear()
    await _fails(view, lambda v: v.click("[data-testid=typo]"), r"The click on <label for=\"a-bx\".*reaches no handler")
    await _fails(view, lambda v: v.click("[data-testid=off]"), r"reaches no handler")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_click_on_a_button_in_a_label_stays_the_buttons():
    view = await mount(DomLabels)
    await view.click("[data-testid=inner-button]")
    assert [name for _, name, _ in HEARD] == ["pressed"]
    HEARD.clear()
    # On the label itself, the click goes on to its first field: the button
    await view.click("[data-testid=holds-button]")
    assert [name for _, name, _ in HEARD] == ["pressed"]


class DomStreamed(Component):
    class Meta:
        template_name = "domprobe/streamed.html"

    async def joined(self) -> None:
        await self.stream("rows", [self._row(1), self._row(2)], dom_id=self._dom_id)

    @staticmethod
    def _row(n: int, label: str = "") -> dict:
        return {"id": n, "label": label or f"row {n}"}

    @staticmethod
    def _dom_id(row: dict) -> str:
        return f"row-{row['id']}"

    async def remove(self, id: int) -> None:
        HEARD.append(("DomStreamed", "remove", {"id": id}))
        await self.stream_delete("rows", f"row-{id}")

    async def add(self, id: int, at: int = -1, limit: int = 0) -> None:
        await self.stream_insert("rows", self._row(id), at=at, limit=limit, dom_id=self._dom_id)

    async def rename(self, id: int, label: str) -> None:
        await self.stream_insert("rows", self._row(id, label), dom_id=self._dom_id)

    async def reset(self) -> None:
        await self.stream("rows", [self._row(9)], reset=True, dom_id=self._dom_id)


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_a_stream_item_is_on_the_page():
    view = await mount(DomStreamed)
    view.clear_messages()
    await view.click("#row-2 [data-testid=row-remove]")
    assert HEARD == [("DomStreamed", "remove", {"id": 2})]
    # The delete the handler streamed took it off the page
    await _fails_after(view, lambda v: v.click("#row-2 button"), r"No element matches '#row-2 button'")
    await view.click("button", text="row 1")
    assert [kwargs["id"] for _, _, kwargs in HEARD] == [2, 1]


async def _fails_after(view, action, match: str) -> None:
    with pytest.raises(AssertionError, match=match):
        await action(view)


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_the_page_holds_the_items_the_operations_left():
    view = await mount(DomStreamed)
    await view.call("add", id=3, at=0)
    await view.call("add", id=4, limit=3)
    # A newer version of an item replaces it where it stands
    await view.call("rename", id=2, label="two")
    page = await _page(view)
    assert _rows(page) == [("row-1", "row 1"), ("row-2", "two"), ("row-4", "row 4")]
    await view.call("reset")
    assert _rows(await _page(view)) == [("row-9", "row 9")]


async def _page(view):
    from asgiref.sync import sync_to_async

    from wireview.testing_dom import Page

    return Page(await sync_to_async(view.render)(), view.component.id, view._wire.sent_messages.stream_ops)


def _rows(page) -> list[tuple[str, str]]:
    return [(li.get("id"), li.text.strip()) for li in Selector("[wire-stream=rows] > li").select(page.root)]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_enter_in_a_form_whose_default_button_is_disabled_submits_nothing():
    view = await mount(DomKeys)
    await _fails(
        view, lambda v: v.keydown("[data-testid=q3]", "Enter"), r"reaches no handler: it dispatched keydown, and"
    )


@pytest.mark.integration
def test_a_key_event_carries_the_fields_a_browser_fills():
    assert key_event("Enter") == {
        "key": "Enter",
        "keyCode": 13,
        "isComposing": False,
        "ctrlKey": False,
        "altKey": False,
        "shiftKey": False,
        "metaKey": False,
    }
    assert key_event("a")["keyCode"] == 65
    assert key_event("Z", shift=True)["keyCode"] == 90
    assert key_event("7")["keyCode"] == 55
    assert key_event("Escape")["keyCode"] == 27
    assert key_event("한")["keyCode"] == 0
    assert key_event("Enter", composing=True)["keyCode"] == 229


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_frozen_component_has_no_page():
    view = await mount(DomBubble)
    view.wire.freeze()
    await _fails(view, lambda v: v.click("button"), r"is frozen")


# ============================================================================
# The parts
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("li", ["a", "b", "c"]),
        ("#second", ["b"]),
        (".odd", ["a", "c"]),
        ("li.odd.last", ["c"]),
        ("[data-testid=x]", ["a"]),
        ("[data-testid='x']", ["a"]),
        ('[data-testid="x"]', ["a"]),
        ("[data-k]", ["a", "b"]),
        ("[data-k^=pre]", ["a"]),
        ("[data-k$=fix]", ["b"]),
        ("[data-k*=e-f]", ["b"]),
        ("[class~=last]", ["c"]),
        ("ul > li", ["a", "b", "c"]),
        ("div > li", []),
        ("div li", ["a", "b", "c"]),
        ("div  >  ul   li.odd", ["a", "c"]),
        ("#second, .last", ["b", "c"]),
        ("*[data-k]", ["a", "b"]),
        ("LI#second", ["b"]),
    ],
)
def test_selectors(selector, expected):
    root = parse(
        "<div><ul>"
        '<li class="odd" data-testid="x" data-k="prelude">a</li>'
        '<li id="second" data-k="the-fix">b</li>'
        '<li class="odd last">c</li>'
        "</ul></div>"
    )
    assert [el.text for el in Selector(selector).select(root)] == expected


@pytest.mark.unit
@pytest.mark.parametrize("selector", ["", "li:first-child", "a + b", "a ~ b", "> a", "a >", "a,", "[x"])
def test_a_selector_the_helpers_do_not_read_says_so(selector):
    with pytest.raises(ValueError):
        Selector(selector)


@pytest.mark.unit
def test_the_parser_closes_what_a_browser_closes():
    root = parse("<ul><li>a<li>b</ul><p>x<input name=q value=1><br>y</p></span>z<textarea>\nkeep</textarea>")
    assert [el.text for el in Selector("li").select(root)] == ["a", "b"]
    assert Selector("p").select(root)[0].text == "xy"
    assert Selector("textarea").select(root)[0].text == "keep"


@pytest.mark.unit
def test_a_selector_names_a_non_ascii_class_and_id():
    root = parse('<p class="강조" id="머리">a</p><p>b</p>')
    assert [el.text for el in Selector("p.강조").select(root)] == ["a"]
    assert [el.text for el in Selector("#머리").select(root)] == ["a"]
