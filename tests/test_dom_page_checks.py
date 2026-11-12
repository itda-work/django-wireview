"""The opt-in checks of the page a mounted component draws: ``WIREVIEW["TEST_PAGE_CHECKS"]`` (GAP-056, #252).

Off by default, so a suite that never asked sees nothing new. ``"warn"`` warns
once for each mistake, ``"raise"`` fails the ``render()`` -- and every DOM
helper, which reads the page through it -- with all of them.
"""

from __future__ import annotations

import warnings

import pytest
from django.core.exceptions import ImproperlyConfigured
from testproj.wireview_setting import set_wireview

from wireview import Component, mount
from wireview.testing_dom import Page, page_problems

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
    problems = page_problems(Page(html, "c1"), lambda name: name == "kept")
    assert [p.split(",")[0] for p in problems] == [
        "<form wire-auto-recover=\"restore\"> recovers through 'restore'",
        "<form wire-auto-recover=\"model_dump\"> recovers through 'model_dump'",
    ]


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
