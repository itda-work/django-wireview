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

from .live import DomBubble, DomForm, DomLeaf, DomLive, DomNest

# The fields DomForm's form holds as the page draws it, as its handlers get them
FORM = {
    "title": "t0",
    "body": "b0",
    "size": "m",
    "tags": ["a", "c"],
    "opts": "x",
    "plan": "free",
    # Disabled fields and every named button go too, as the client sends them
    "locked": "L",
    "secret": "h",
    "query": "",
    "action": "delete",
}


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
        """What the page's ``heard`` shows once the last expected handler ran."""
        return re.compile(rf"\b{re.escape(self.expected[-1][0])}$")


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


BUBBLE = {"note": "n1"}
WRAPPED = ("wrapped", {**BUBBLE, "where": "section"})

SCENARIOS = [
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
    # A submit button submits its form: every field, every named button
    Scenario("save", DomForm, "heard-form", lambda v: v.click("[data-testid=save]"), _click("save"), [("saved", FORM)]),
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
        [("saved", {**FORM, "title": "new", "agree": "on", "plan": "pro", "tags": ["b"], "opts": "y"})],
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


def heard(component: type, expected: list[tuple[str, dict[str, t.Any]]], log: list[t.Any]) -> list[t.Any]:
    """What ``component``'s handlers named in ``expected`` heard, in order."""
    names = {name for name, _ in expected}
    return [(name, kwargs) for cls, name, kwargs in log if cls == component.__name__ and name in names]
