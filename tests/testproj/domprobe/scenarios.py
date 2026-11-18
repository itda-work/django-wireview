"""The user actions on the dom probe, each written twice: for the test helpers and for a browser.

``tests/test_dom_events.py`` runs ``helper`` on a component mounted alone and
``tests/test_dom_events_e2e.py`` runs ``browser`` on the page; each compares what
the handlers of ``component`` heard with ``expected``. A browser action may
hear more than the helper's (a typed field's own ``change`` before the submit),
so only the handlers in ``expected`` are compared, in order.
"""

from __future__ import annotations

import dataclasses
import re
import typing as t

from .live import DomBubble, DomForm, DomKeys, DomLeaf, DomLive, DomMeta, DomNest

# The fields DomForm's form holds as the page draws it, as its handlers get them
FORM = {
    "title": "t0",
    "body": "b0",
    "size": "m",
    "tags": ["a", "c"],
    "opts": "x",
    "plan": "free",
    # A disabled field stays home, and a named button goes only when it submitted (#244)
    "secret": "h",
    "query": "",
}
# What a submit with the save button sends: the form and the button that submitted
SAVED = {**FORM, "action": "save"}


@dataclasses.dataclass(frozen=True)
class Scenario:
    name: str
    component: type
    #: The testid of the ``heard`` output the browser waits on
    output: str
    helper: t.Callable[[t.Any], t.Awaitable[t.Any]]
    browser: t.Callable[[t.Any], None]
    expected: list[tuple[str, dict[str, t.Any]]]

    @property
    def done(self) -> re.Pattern[str]:
        """What the page's ``heard`` shows once the last expected handler ran (others may run after it)."""
        return re.compile(rf"\b{re.escape(self.expected[-1][0])}\b")


def by(page: t.Any, testid: str) -> t.Any:
    return page.get_by_test_id(testid)


def _click(testid: str) -> t.Callable[[t.Any], None]:
    return lambda page: by(page, testid).click()


def _fill_and_save(page: t.Any) -> None:
    by(page, "title").fill("new")
    by(page, "agree").check()
    by(page, "plan-pro").check()
    by(page, "tags").select_option(["b"])
    by(page, "opt-x").uncheck()
    by(page, "opt-y").check()
    by(page, "save").click()


def _type_and_leave(page: t.Any) -> None:
    by(page, "title").fill("typed")
    # Leaving the field commits it: the browser sends change
    by(page, "title").press("Tab")


async def _pick_and_reset_helper(view: t.Any) -> None:
    await view.change("[data-testid=size]", "s")
    await view.click("[data-testid=reset]")


def _pick_and_reset(page: t.Any) -> None:
    from testproj.e2e_browser import expect_text

    by(page, "size").select_option("s")
    expect_text(by(page, "heard-form"), "changed")
    by(page, "reset").click()
    expect_text(by(page, "heard-form"), "changed changed")


# DomMeta's form as the page draws it: the hidden field goes, the disabled one stays home
META_FORM = {"name": "", "email": "", "age": "", "token": "t"}


def _meta(target: str | None, unused: list[str], bad: list[str] = [], age: str = "") -> dict[str, t.Any]:
    return {"target": target, "unused": unused, "bad": bad, "age": age}


async def _meta_leave_helper(view: t.Any) -> None:
    await view.input("[data-testid=meta-name]", "a")
    await view.change("[data-testid=meta-name]", "a")
    await view.input("[data-testid=meta-email]", "b")


def _meta_leave(page: t.Any) -> None:
    from testproj.e2e_browser import expect_text

    by(page, "meta-name").fill("a")
    expect_text(by(page, "heard-meta"), "typed")
    # Tab: change, then the blur that leaves the field used
    by(page, "meta-name").press("Tab")
    expect_text(by(page, "heard-meta"), "typed changed")
    by(page, "meta-email").fill("b")


async def _meta_number_helper(view: t.Any) -> None:
    await view.input("[data-testid=meta-age]", "1")
    await view.input("[data-testid=meta-age]", "1e")


def _meta_number(page: t.Any) -> None:
    # One keystroke, one input: "1", then "1e", which is no number -- the browser sends ""
    by(page, "meta-age").press_sequentially("1e")


async def _meta_reset_helper(view: t.Any) -> None:
    await view.input("[data-testid=meta-name]", "a")
    await view.change("[data-testid=meta-name]", "a")
    await view.click("[data-testid=meta-reset]")


def _meta_reset(page: t.Any) -> None:
    from testproj.e2e_browser import expect_text

    by(page, "meta-name").fill("a")
    expect_text(by(page, "heard-meta"), "typed")
    by(page, "meta-name").press("Tab")
    expect_text(by(page, "heard-meta"), "typed changed")
    by(page, "meta-reset").click()
    expect_text(by(page, "heard-meta"), "typed changed typed changed")


BUBBLE = {"note": "n1"}
WRAPPED = ("wrapped", {**BUBBLE, "where": "section"})

SCENARIOS = [
    # A handler that names _target, _unused and _bad_input gets them; one that takes **data gets the form (#244).
    # The field typed in is the target; the fields nobody has changed or left are unused, the target aside;
    # the hidden and the disabled field are no one's
    Scenario(
        "meta-leave",
        DomMeta,
        "heard-meta",
        _meta_leave_helper,
        _meta_leave,
        [
            ("typed", _meta("name", ["email", "age"])),
            ("changed", {**META_FORM, "name": "a"}),
            ("typed", _meta("email", ["age"])),
        ],
    ),
    # What a number field cannot read is sent as "", and named as bad input
    Scenario(
        "meta-number",
        DomMeta,
        "heard-meta",
        _meta_number_helper,
        _meta_number,
        [
            ("typed", _meta("age", ["name", "email"], age="1")),
            ("typed", _meta("age", ["name", "email"], bad=["age"])),
        ],
    ),
    # A reset forgets what was used in the form: its input comes from the form, no field's
    Scenario(
        "meta-reset",
        DomMeta,
        "heard-meta",
        _meta_reset_helper,
        _meta_reset,
        [
            ("typed", _meta("name", ["email", "age"])),
            ("changed", {**META_FORM, "name": "a"}),
            ("typed", _meta(None, ["name", "email", "age"])),
            ("changed", META_FORM),
        ],
    ),
    # A submit uses every field of its form, and comes from no field
    Scenario(
        "meta-save",
        DomMeta,
        "heard-meta",
        lambda v: v.click("[data-testid=meta-save]"),
        _click("meta-save"),
        [("saved", {"target": None, "unused": [], **META_FORM})],
    ),
    # A reset puts the fields back as the render drew them -- the size the change kept -- and the form's
    # change hears them (#244); the reset button itself never goes
    Scenario(
        "reset",
        DomForm,
        "heard-form",
        _pick_and_reset_helper,
        _pick_and_reset,
        [("changed", {**FORM, "size": "s"}), ("changed", {**FORM, "size": "s"})],
    ),
    # A binding on the element, then the one on its ancestor
    Scenario(
        "inner",
        DomBubble,
        "heard-bubble",
        lambda v: v.click("[data-testid=inner]"),
        _click("inner"),
        [("inner", {**BUBBLE, "n": 1}), WRAPPED],
    ),
    # .stop ends the bubbling after the element
    Scenario(
        "stop",
        DomBubble,
        "heard-bubble",
        lambda v: v.click("[data-testid=stopper]"),
        _click("stopper"),
        [("inner", {**BUBBLE, "n": 2})],
    ),
    # No binding of its own: the ancestor's
    Scenario("bare", DomBubble, "heard-bubble", lambda v: v.click("[data-testid=bare]"), _click("bare"), [WRAPPED]),
    # A plain click does not hold for .ctrl: on to the ancestor
    Scenario("ctrl", DomBubble, "heard-bubble", lambda v: v.click("[data-testid=ctrl]"), _click("ctrl"), [WRAPPED]),
    # A debounced binding fires after the dispatch, behind the ancestor's
    Scenario(
        "debounce",
        DomBubble,
        "heard-bubble",
        lambda v: v.click("[data-testid=slow]"),
        _click("slow"),
        [WRAPPED, ("inner", {**BUBBLE, "n": 4})],
    ),
    # A JS() chain without a push sends nothing; the ancestor's binding does
    Scenario("js", DomBubble, "heard-bubble", lambda v: v.click("[data-testid=js]"), _click("js"), [WRAPPED]),
    # A submit button submits its form: every enabled field, and that button
    Scenario(
        "save", DomForm, "heard-form", lambda v: v.click("[data-testid=save]"), _click("save"), [("saved", SAVED)]
    ),
    Scenario(
        "submit-values",
        DomForm,
        "heard-form",
        lambda v: v.submit(
            "form",
            values={"title": "new", "agree": True, "plan": "pro", "tags": ["b"], "opts": ["y"]},
            submitter="[data-testid=save]",
        ),
        _fill_and_save,
        [("saved", {**SAVED, "title": "new", "agree": "on", "plan": "pro", "tags": ["b"], "opts": "y"})],
    ),
    # A change bubbles to the form's binding; the form's fields go with it
    Scenario(
        "change",
        DomForm,
        "heard-form",
        lambda v: v.change("[data-testid=title]", "typed"),
        _type_and_leave,
        [("changed", {**FORM, "title": "typed"})],
    ),
    Scenario(
        "input",
        DomForm,
        "heard-form",
        lambda v: v.input("[data-testid=query]", "abc"),
        lambda page: by(page, "query").fill("abc"),
        [("typed", {**FORM, "query": "abc"})],
    ),
    # A click on a checkbox ticks it, then change bubbles
    Scenario(
        "checkbox",
        DomForm,
        "heard-form",
        lambda v: v.click("[data-testid=agree]"),
        _click("agree"),
        [("changed", {**FORM, "agree": "on"})],
    ),
    Scenario(
        "radio",
        DomForm,
        "heard-form",
        lambda v: v.click("[data-testid=plan-pro]"),
        _click("plan-pro"),
        [("changed", {**FORM, "plan": "pro"})],
    ),
    # Outside the form, the scope is the component: its fields and the form's
    Scenario(
        "outside",
        DomForm,
        "heard-form",
        lambda v: v.click("[data-testid=outside-click]"),
        _click("outside-click"),
        [("clicked", {**FORM, "outside": "o"})],
    ),
    # A click in a nested component with no binding of its own reaches the
    # parent's binding, and the nested component's field stays home
    Scenario(
        "nested",
        DomNest,
        "heard-nest",
        lambda v: v.click("[data-testid=leaf-bare]"),
        _click("leaf-bare"),
        [("wrapped", {"mine": "m1"})],
    ),
    # The nested component's own binding goes to it, with its own field
    Scenario(
        "leaf",
        DomLeaf,
        "heard-leaf",
        lambda v: v.click("[data-testid=leaf-button]"),
        _click("leaf-button"),
        [("leaf", {"leafy": "l1", "k": "v"})],
    ),
    # myself=True: the LiveComponent's handler, with the LiveComponent's field
    Scenario(
        "live",
        DomLive,
        "heard-live",
        lambda v: v.click("[data-testid=live-button]"),
        _click("live-button"),
        [("bump", {"lively": "v1"})],
    ),
]


# DomKeys' fields outside its forms: every named one in the component, its forms' included
# The component's fields; the send form's button goes only with a submit it made
KEYS = {"draft": "d0", "subject": "s0", "q": "q0", "q2": "r0", "q3": "x0"}

SCENARIOS += [
    # A modifier key held during the click
    Scenario(
        "shift-click",
        DomBubble,
        "heard-bubble",
        lambda v: v.click("[data-testid=shift]", shift=True),
        lambda page: by(page, "shift").click(modifiers=["Shift"]),
        [("inner", {**BUBBLE, "n": 5}), WRAPPED],
    ),
    # .enter on keyup; a browser's press is keydown then keyup
    Scenario(
        "keyup-enter",
        DomKeys,
        "heard-keys",
        lambda v: v.keyup("[data-testid=draft]", "Enter"),
        lambda page: by(page, "draft").press("Enter"),
        [("sent", KEYS)],
    ),
    Scenario(
        "keydown-escape",
        DomKeys,
        "heard-keys",
        lambda v: v.keydown("[data-testid=draft]", "Escape"),
        lambda page: by(page, "draft").press("Escape"),
        [("cancelled", KEYS)],
    ),
    Scenario(
        "ctrl-enter",
        DomKeys,
        "heard-keys",
        lambda v: v.keydown("[data-testid=draft]", "Enter", ctrl=True),
        lambda page: by(page, "draft").press("Control+Enter"),
        [("ctrl_sent", KEYS)],
    ),
    # .key_code.65 on an ancestor: the key "a" bubbles up with keyCode 65
    Scenario(
        "key-code",
        DomKeys,
        "heard-keys",
        lambda v: v.keydown("[data-testid=draft]", "a"),
        lambda page: by(page, "draft").press("a"),
        [("letter", KEYS)],
    ),
    # Enter in a form's only text field submits it
    Scenario(
        "implicit-submit",
        DomKeys,
        "heard-keys",
        lambda v: v.keydown("[data-testid=q]", "Enter"),
        lambda page: by(page, "q").press("Enter"),
        [("searched", {"q": "q0"})],
    ),
    # ...and in a form with a submit button, clicks that button
    Scenario(
        "default-button",
        DomKeys,
        "heard-keys",
        lambda v: v.keydown("[data-testid=q2]", "Enter"),
        lambda page: by(page, "q2").press("Enter"),
        [("submitted", {"q2": "r0", "go": "1"})],
    ),
    # focus runs the field's own binding and not its ancestor's; focusin bubbles
    Scenario(
        "focus",
        DomKeys,
        "heard-keys",
        lambda v: v.focus("[data-testid=subject]"),
        lambda page: by(page, "subject").focus(),
        [("focused", {**KEYS, "where": "field"}), ("entered", KEYS)],
    ),
    Scenario(
        "blur",
        DomKeys,
        "heard-keys",
        lambda v: v.blur("[data-testid=subject]"),
        lambda page: (by(page, "subject").focus(), by(page, "subject").blur()),
        [("blurred", KEYS), ("left", KEYS)],
    ),
]


SCENARIOS += [
    # A label's click goes on to its field: for= names it, or the label holds it
    Scenario(
        "label-for",
        DomForm,
        "heard-form",
        lambda v: v.click("[data-testid=agree-label]"),
        _click("agree-label"),
        [("changed", {**FORM, "agree": "on"})],
    ),
    Scenario(
        "label-wraps",
        DomForm,
        "heard-form",
        lambda v: v.click("[data-testid=pro-text]"),
        _click("pro-text"),
        [("changed", {**FORM, "plan": "pro"})],
    ),
    # A label of a submit button submits its form
    Scenario(
        "label-button",
        DomForm,
        "heard-form",
        lambda v: v.click("[data-testid=save-label]"),
        _click("save-label"),
        [("saved", SAVED)],
    ),
]


# What DomHooked hears from its chart's hook pushing ``point`` with x=3, and the reply the hook gets
HOOK_HEARD = [("point", {"hook": "DomPush", "x": 3})]
HOOK_REPLY = {"event": "point", "doubled": 6, "owner_is_me": True}


def heard(component: type, expected: list[tuple[str, dict[str, t.Any]]], log: list[t.Any]) -> list[t.Any]:
    """What ``component``'s handlers named in ``expected`` heard, in order."""
    names = {name for name, _ in expected}
    return [(name, kwargs) for cls, name, kwargs in log if cls == component.__name__ and name in names]


@dataclasses.dataclass(frozen=True)
class Payload:
    """A form of ``DomPayload``, the button that submits it, and the fields its submit sends.

    ``sent`` is in a submission's terms: each name with its values in order, a
    select-multiple's chosen values one by one -- what the browser's
    ``FormData(form, submitter)`` holds (#244).
    """

    name: str
    form: str
    submitter: str | None
    sent: dict[str, list[str]]


_BASIC = {
    "text": ["t"],
    "num": ["3"],
    "area": ["a"],
    "one": ["y"],
    "many": ["p", "r"],
    "box": ["on1"],
    "plain": ["on"],
    "pick": ["b"],
    "secret": ["h"],
    "dotted.name": ["d"],
    "items.0": ["i0"],
    "items.1": ["i1"],
}

PAYLOADS = [
    # The button that submits goes, the other one with its name does not; reset and plain buttons never do
    Payload("first-button", "f-basic", "f-basic-save", {**_BASIC, "action": ["save"]}),
    Payload("second-button", "f-basic", "f-basic-delete", {**_BASIC, "action": ["delete"]}),
    Payload("submit-input", "f-basic", "f-basic-go", {**_BASIC, "go": ["Go"]}),
    Payload("no-button", "f-basic", None, _BASIC),
    Payload("disabled", "f-disabled", "f-disabled-submit", {"on": ["1"], "legend": ["l"], "open": ["o"], "b": ["1"]}),
    Payload("form-attribute", "f-owner", "f-owner-submit", {"inner": ["in"], "outer": ["out"], "outerbutton": ["ob"]}),
    Payload("owned-elsewhere", "f-other", None, {"elsewhere": ["away"], "mine": ["m"]}),
    Payload("image", "f-image", "f-image-pic", {"q": ["v"], "pic.x": ["0"], "pic.y": ["0"]}),
]


def submitted(implicit_args: dict[str, list[t.Any]]) -> dict[str, list[str]]:
    """A ``user_event``'s ``implicit_args`` in a submission's terms: a select-multiple's list spread out."""
    return {
        name: [str(v) for value in values for v in (value if isinstance(value, list) else [value])]
        for name, values in implicit_args.items()
    }


# ============================================================================
# A held page: DomFamily, its DomKid and its DomPet, step by step (#252)
# ============================================================================

#: The classes of the held page, whose hooks and handlers ``FAMILY`` compares
FAMILY_CLASSES = ("DomFamily", "DomKid", "DomPet")


@dataclasses.dataclass(frozen=True)
class Step:
    """One user action on the family page, and what it leaves.

    ``heard`` is every hook and handler of ``FAMILY_CLASSES`` that ran, in
    order; ``shows`` the text of each ``data-testid`` after it (``None``: not
    on the page).
    """

    name: str
    testid: str
    heard: list[tuple[str, str, dict[str, t.Any]]]
    shows: dict[str, str | None]


#: What the page's first joins run: the nested component's join, the LiveComponent's in its parent's render
FAMILY_JOINED = [("DomKid", "joined", {}), ("DomPet", "joined", {})]

FAMILY = [
    # The nested component's own event: its count, which the page keeps
    Step("kid-bump", "kid-bump", [("DomKid", "bump", {})], {"kid-text": "a:1", "pet-text": "a:0"}),
    # The LiveComponent's (myself=True)
    Step("pet", "pet-pet", [("DomPet", "pet", {})], {"pet-text": "a:1", "kid-text": "a:1"}),
    # send_to_parent: the parent's handler runs, with the instance it drew
    Step(
        "tell",
        "pet-tell",
        [("DomPet", "tell", {}), ("DomFamily", "told_me", {"n": 1})],
        {"family-told": "1", "pet-text": "a:1"},
    ),
    # The parent passes new props: update() for the LiveComponent, the props for the nested one, whose count stays
    Step(
        "rename",
        "rename",
        [("DomFamily", "rename", {}), ("DomPet", "update", {"title": "ab"})],
        {"family-title": "ab", "kid-text": "ab:1", "pet-text": "ab:1"},
    ),
    # The parent stops drawing the LiveComponent: leaving()
    Step("hide", "toggle", [("DomFamily", "toggle", {}), ("DomPet", "leaving", {})], {"pet-text": None}),
    # ...and draws it again: a new instance, joined, from its first state
    Step("show", "toggle", [("DomFamily", "toggle", {}), ("DomPet", "joined", {})], {"pet-text": "ab:0"}),
    # The new one hears its own events
    Step("pet-again", "pet-pet", [("DomPet", "pet", {})], {"pet-text": "ab:1", "kid-text": "ab:1"}),
]


def family_heard(log: list[t.Any]) -> list[tuple[str, str, dict[str, t.Any]]]:
    """What the held page's components heard, in order."""
    return [entry for entry in log if entry[0] in FAMILY_CLASSES]


# ============================================================================
# A form's own submission over HTTP: submit_form() and the browser (#252)
# ============================================================================


@dataclasses.dataclass(frozen=True)
class HttpSubmit:
    """A form of ``DomHttp``, the button that submits it, and what ``views.received`` gets.

    ``fields`` is each name with ``getlist(name)``: two fields of one name with
    the pressed button of that name between them keep that order.
    """

    name: str
    form: str
    submitter: str
    method: str
    fields: dict[str, list[str]]


_POSTED = {"blank": [""], "note": ["line1\r\nline2"], "many": ["p", "r"], "outside": ["o"]}

HTTP_SUBMITS = [
    HttpSubmit("post", "post-form", "post-pick", "POST", {"pick": ["a", "b", "c"], **_POSTED}),
    HttpSubmit("post-other-button", "post-form", "post-other", "POST", {"pick": ["a", "c"], "other": ["x"], **_POSTED}),
    HttpSubmit("formmethod-get", "post-form", "post-as-get", "GET", {"pick": ["a", "c"], "how": ["get"], **_POSTED}),
    # The action's own query is replaced by the fields
    HttpSubmit("get", "get-form", "get-go", "GET", {"q": ["1", "2"], "away": [""]}),
    # multipart, sent to the submitter's relative formaction; a file field with no file is an empty part,
    # which Django reads as an empty field
    HttpSubmit("multipart", "multi-form", "multi-pick", "POST", {"pick": ["a", "b", "c"], "doc": [""]}),
]
