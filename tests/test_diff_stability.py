"""Regression tests: partial diffs must survive the signed state attribute.

``{% tag_header %}`` embeds a freshly signed ``data-state`` on every render.
If that value lands in the static parts, the fingerprint changes on every
render and every event ships a full render. These tests render through the
real tag and assert that the second render is a partial diff.
"""

import html
import json
import re

import pytest
from django.template import Template

from wireview import Component
from wireview.core.state import sign_state, unsign_state
from wireview.testing import mount

TEMPLATE_SOURCE = (
    "{% load wireview %}"
    '<div {% tag_header %}><h1>{{ title }}</h1><p class="count">{{ count }}</p>'
    '<button {% on "click" "increment" %}>+</button></div>'
)
_template: Template | None = None
STATE_ATTR = re.compile(r'data-state="([^"]*)"')


class DiffProbe(Component):
    _template_name = "diff_probe.html"

    title: str = "Probe"
    count: int = 0

    async def increment(self):
        self.count += 1

    @classmethod
    def _get_template(cls, template_name=None):
        global _template
        if _template is None:
            _template = Template(TEMPLATE_SOURCE)
        return _template


def _unsigned_state(rendered_html: str) -> dict:
    match = STATE_ATTR.search(rendered_html)
    assert match, rendered_html
    raw = html.unescape(match.group(1))
    assert "<!--" not in raw, "marker leaked into the attribute value"
    return unsign_state(raw)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_live_render_diff_is_partial_after_state_change():
    view = await mount(DiffProbe)
    view._repo.is_live = True
    wire, comp, repo = view.wire, view.component, view._repo

    first = await wire.render_diff(comp, repo)
    assert first is not None and "s" in first, "first render must be a full render"
    # title, count and the signed state are the dynamic parts
    assert len(first["d"]) == 3

    await view.call("increment")
    second = await wire.render_diff(comp, repo)

    assert second is not None and "s" not in second, f"expected a partial diff, got {second}"
    assert "1" in second.values()
    assert len(json.dumps(second)) < len(json.dumps(first)) / 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_live_render_diff_is_none_when_nothing_changed():
    view = await mount(DiffProbe)
    view._repo.is_live = True
    wire, comp, repo = view.wire, view.component, view._repo

    await wire.render_diff(comp, repo)
    assert await wire.render_diff(comp, repo) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_state_attribute_stays_valid_for_reconnect_after_partial_diff():
    view = await mount(DiffProbe)
    view._repo.is_live = True
    wire, comp, repo = view.wire, view.component, view._repo

    await wire.render_diff(comp, repo)
    await view.call("increment")
    await wire.render_diff(comp, repo)

    # What the client rebuilds from statics + dynamics is what lands in the DOM,
    # and what it sends back on reconnect.
    rebuilt = wire._last_rendered.to_html()
    assert _unsigned_state(rebuilt)["count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_http_render_keeps_plain_state_attribute():
    view = await mount(DiffProbe, count=7)
    assert view._repo.is_live is False

    rendered = str(view.render())
    assert _unsigned_state(rendered)["count"] == 7


@pytest.mark.asyncio
@pytest.mark.unit
async def test_signed_state_round_trips_and_accepts_legacy_format():
    from django.core.signing import Signer

    view = await mount(DiffProbe, count=3)
    component = view.component

    state = unsign_state(sign_state(component))
    assert state["count"] == 3 and state["id"] == component.id
    assert "wire" not in state and "user" not in state

    legacy = Signer().sign(component.model_dump_json(exclude=component._exclude_fields))
    assert unsign_state(legacy) == state


@pytest.mark.asyncio
@pytest.mark.unit
async def test_signed_state_is_deterministic_and_compact_for_lists():
    from django.core.signing import Signer

    class ListProbe(DiffProbe):
        items: list[dict] = []

    items = [{"name": f"item {i}", "qty": i} for i in range(50)]
    view = await mount(ListProbe, items=items)
    component = view.component

    assert sign_state(component) == sign_state(component)
    legacy = Signer().sign(component.model_dump_json(exclude=component._exclude_fields))
    assert len(sign_state(component)) < len(legacy) / 3
