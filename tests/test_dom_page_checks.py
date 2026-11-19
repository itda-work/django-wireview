"""The opt-in checks of the page a mounted component draws: ``WIREVIEW["TEST_PAGE_CHECKS"]`` (GAP-056, #252).

Off by default, so a suite that never asked sees nothing new. ``"warn"`` warns
once for each mistake, ``"raise"`` fails the ``render()`` -- and every DOM
helper, which reads the page through it -- with all of them.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from testproj.wireview_setting import set_wireview

from wireview import Component, mount
from wireview.testing_dom import RECOVERY_OFF, Page, page_problems

pytestmark = pytest.mark.integration


class DomChecked(Component):
    """Every mistake the checks name, once."""

    class Meta:
        template_name = "domprobe/checked.html"

    async def changed(self, **kwargs) -> None:
        pass


class DomClean(Component):
    """The same kinds of element, without the mistakes."""

    class Meta:
        template_name = "domprobe/clean.html"

    async def changed(self, **kwargs) -> None:
        pass

    async def restore(self, form_data: dict) -> None:
        pass


@pytest.mark.asyncio
async def test_nothing_is_checked_by_default():
    view = await mount(DomChecked)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert view.render()
        await view.call("changed")


@pytest.mark.asyncio
async def test_raise_names_every_mistake(monkeypatch):
    set_wireview(monkeypatch, TEST_PAGE_CHECKS="raise")
    view = await mount(DomChecked)
    with pytest.raises(AssertionError) as raised:
        view.render()
    message = str(raised.value)
    assert "has 3 problem(s)" in message
    assert "2 elements have the id 'twice'" in message
    assert "2 components have the id 'twin' (DomHookLeaf, DomHookLeaf)" in message
    assert 'data-testid="bare"> has a bare wire-auto-recover' in message
    # The form whose change is bound recovers
    assert 'data-testid="bound"' not in message


def test_a_recover_handler_the_client_cannot_call_is_named():
    # Not a template of the repository: the template diagnostics (wireview_check_templates)
    # report these already, and the repository's templates must have nothing to report
    html = (
        '<div id="c1" wireview-component data-name="X">'
        '<form wire-auto-recover="restore"><input name="a"></form>'
        '<form wire-auto-recover="model_dump"><input name="b"></form>'
        '<form wire-auto-recover="kept"><input name="c"></form>'
        '<form wire-auto-recover="true" wire-on-change=\'{"h":"changed"}\'><input name="d"></form>'
        "</div>"
    )
    problems = page_problems(Page(html, "c1"), lambda component_id, name: name == "kept")
    assert {check for check, _ in problems} == {"recover_form"}
    assert [p.split(",")[0] for _, p in problems] == [
        "<form wire-auto-recover=\"restore\"> recovers through 'restore'",
        "<form wire-auto-recover=\"model_dump\"> recovers through 'model_dump'",
    ]


def test_a_form_whose_recovery_is_off_is_not_a_problem():
    """``false`` turns the recovery off (#245); ``ignore`` is still a handler's name, checked as one."""
    html = (
        '<div id="c1" wireview-component data-name="X">'
        '<form wire-auto-recover="false"><input name="a"></form>'
        '<form wire-auto-recover="ignore"><input name="b"></form>'
        "</div>"
    )
    problems = page_problems(Page(html, "c1"), lambda component_id, name: False)
    assert [p.split(",")[0] for _, p in problems] == ["<form wire-auto-recover=\"ignore\"> recovers through 'ignore'"]


def test_the_value_that_turns_recovery_off_is_the_clients():
    source = (Path(__file__).parents[1] / "wireview/static/wireview/recovery.mjs").read_text(encoding="utf-8")
    assert f'export const RECOVERY_OFF = "{RECOVERY_OFF}";' in source


@pytest.mark.asyncio
async def test_a_helper_reads_the_page_through_the_checks(monkeypatch):
    set_wireview(monkeypatch, TEST_PAGE_CHECKS="raise")
    view = await mount(DomChecked)
    with pytest.raises(AssertionError, match="problem"):
        await view.change("[data-testid=bound] input", "x")


@pytest.mark.asyncio
async def test_warn_warns_once_a_mistake(monkeypatch):
    set_wireview(monkeypatch, TEST_PAGE_CHECKS="warn")
    view = await mount(DomChecked)
    with pytest.warns(UserWarning, match="^wireview TEST_PAGE_CHECKS: ") as record:
        assert view.render()
    assert len(record) == 3


@pytest.mark.asyncio
async def test_a_page_without_mistakes_passes(monkeypatch):
    set_wireview(monkeypatch, TEST_PAGE_CHECKS="raise")
    view = await mount(DomClean)
    assert view.render()


@pytest.mark.asyncio
async def test_an_unknown_policy_is_refused(monkeypatch):
    set_wireview(monkeypatch, TEST_PAGE_CHECKS="strict")
    view = await mount(DomClean)
    with pytest.raises(ImproperlyConfigured, match='takes "ignore", "warn" or "raise"'):
        view.render()


@pytest.mark.asyncio
async def test_each_check_has_its_own_policy(monkeypatch):
    """Phoenix's test_warnings: raise one kind of mistake, warn another, leave the third."""
    set_wireview(
        monkeypatch,
        TEST_PAGE_CHECKS={"duplicate_component": "raise", "duplicate_id": "warn"},
    )
    view = await mount(DomChecked)
    with pytest.warns(UserWarning, match="2 elements have the id 'twice'") as record:
        with pytest.raises(AssertionError) as raised:
            view.render()
    assert len(record) == 1
    message = str(raised.value)
    assert "has 1 problem(s)" in message and "(duplicate_component)" in message
    # recover_form is left out of the dict: ignored
    assert "wire-auto-recover" not in message


@pytest.mark.asyncio
async def test_a_form_can_be_left_out_of_the_recover_check(monkeypatch):
    set_wireview(
        monkeypatch,
        TEST_PAGE_CHECKS={"recover_form": "raise", "skip_forms": ["form[data-testid=bare]"]},
    )
    view = await mount(DomChecked)
    assert view.render()
    set_wireview(monkeypatch, TEST_PAGE_CHECKS={"recover_form": "raise"})
    with pytest.raises(AssertionError, match="bare wire-auto-recover"):
        view.render()


@pytest.mark.asyncio
async def test_the_recover_check_reads_a_nested_components_handlers(monkeypatch):
    """A held page knows its nested components: a recover form of one is checked against its handlers (#252)."""
    set_wireview(monkeypatch, TEST_PAGE_CHECKS={"recover_form": "raise"})
    page = Page(
        '<div id="p" wireview-component data-name="P"><div id="n" wireview-component data-name="N">'
        '<form wire-auto-recover="restore"><input name="a"></form></div></div>',
        "p",
    )
    asked: list[tuple[str, str]] = []

    def callable_handler(component_id: str, name: str) -> bool | None:
        asked.append((component_id, name))
        return None

    # Nobody can say for the nested one: nothing is reported
    assert page_problems(page, callable_handler) == []
    assert asked == [("n", "restore")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "match"),
    [
        ({"duplicates": "raise"}, r"names \['duplicates'\], which are not checks"),
        ({"duplicate_id": "error"}, r"sets \{'duplicate_id': 'error'\}"),
        ({"skip_forms": "form"}, r"is a list of CSS selectors"),
        ({"skip_forms": ["form:nth-child(2)"]}, r"skip_forms"),
        (["raise"], r"or a dict of them"),
    ],
)
async def test_a_setting_the_checks_cannot_read_is_refused(monkeypatch, setting, match):
    set_wireview(monkeypatch, TEST_PAGE_CHECKS=setting)
    view = await mount(DomClean)
    with pytest.raises(ImproperlyConfigured, match=match):
        view.render()
