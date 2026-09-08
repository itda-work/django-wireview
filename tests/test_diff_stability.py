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


LOOP_TEMPLATE_SOURCE = (
    "{% load wireview %}"
    "<div {% tag_header %}><h1>{{ title }}</h1><p>{{ note }}</p>"
    "<ul>{% for item in items %}"
    '<li class="{% if item.done %}done{% endif %}">{{ item.name }} x {{ item.qty }}</li>'
    "{% empty %}<li>none</li>{% endfor %}</ul>"
    "{% if note %}<footer>{{ note }}</footer>{% endif %}"
    "</div>"
)
_loop_template: Template | None = None


class LoopProbe(Component):
    _template_name = "loop_probe.html"

    title: str = "Loop"
    note: str = ""
    items: list[dict] = []

    async def append_item(self):
        self.items.append({"name": f"item {len(self.items)}", "qty": 1})

    async def bump_first(self):
        self.items[0]["qty"] += 1

    async def toggle_first(self):
        self.items[0]["done"] = not self.items[0].get("done", False)

    @classmethod
    def _get_template(cls, template_name=None):
        global _loop_template
        if _loop_template is None:
            _loop_template = Template(LOOP_TEMPLATE_SOURCE)
        return _loop_template


async def _live(component_class, **state):
    view = await mount(component_class, **state)
    view._repo.is_live = True
    return view


@pytest.mark.asyncio
@pytest.mark.unit
async def test_appending_a_loop_item_is_a_partial_diff():
    view = await _live(LoopProbe, items=[{"name": "a", "qty": 1}])
    wire, comp, repo = view.wire, view.component, view._repo
    first = await wire.render_diff(comp, repo)
    assert first is not None and "s" in first

    await view.call("append_item")
    diff = await wire.render_diff(comp, repo)

    assert diff is not None and "s" not in diff, diff
    (change,) = [v for v in diff.values() if isinstance(v, dict) and "u" in v]
    assert change["n"] == 2 and list(change["u"]) == ["1"]
    assert len(json.dumps(diff)) < len(json.dumps(first))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_changing_one_loop_item_sends_only_that_item():
    view = await _live(LoopProbe, items=[{"name": "a", "qty": 1}, {"name": "b", "qty": 1}])
    wire, comp, repo = view.wire, view.component, view._repo
    await wire.render_diff(comp, repo)

    await view.call("bump_first")
    diff = await wire.render_diff(comp, repo)

    assert diff is not None and "s" not in diff
    (change,) = [v for v in diff.values() if isinstance(v, dict) and "u" in v]
    assert change == {"u": {"0": [{"r": [""], "d": []}, "a", "2"]}, "n": 2}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_toggling_a_conditional_inside_an_item_is_a_partial_diff():
    view = await _live(LoopProbe, items=[{"name": "a", "qty": 1}, {"name": "b", "qty": 1}])
    wire, comp, repo = view.wire, view.component, view._repo
    await wire.render_diff(comp, repo)

    await view.call("toggle_first")
    diff = await wire.render_diff(comp, repo)

    assert diff is not None and "s" not in diff, diff
    (change,) = [v for v in diff.values() if isinstance(v, dict) and "u" in v]
    assert change == {"u": {"0": [{"r": ["done"], "d": []}, "a", "1"]}, "n": 2}
    assert 'class="done"' in wire._last_rendered.to_html()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_top_level_if_toggle_is_a_partial_diff():
    view = await _live(LoopProbe, items=[{"name": "a", "qty": 1}])
    wire, comp, repo = view.wire, view.component, view._repo
    await wire.render_diff(comp, repo)

    comp.note = "hello"
    diff = await wire.render_diff(comp, repo)

    assert diff is not None and "s" not in diff, diff
    assert {"r": ["<footer>", "</footer>"], "d": ["hello"]} in diff.values()
    assert "<footer>hello</footer>" in wire._last_rendered.to_html()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_empty_loop_then_first_item_stays_partial():
    view = await _live(LoopProbe)
    wire, comp, repo = view.wire, view.component, view._repo
    first = await wire.render_diff(comp, repo)
    assert "<li>none</li>" in wire._last_rendered.to_html()

    await view.call("append_item")
    diff = await wire.render_diff(comp, repo)

    assert diff is not None and "s" not in diff
    assert "<li>none</li>" not in wire._last_rendered.to_html()
    assert len(json.dumps(diff)) < len(json.dumps(first))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_variable_toggling_from_empty_keeps_indexes_stable():
    view = await _live(LoopProbe, items=[{"name": "a", "qty": 1}])
    wire, comp, repo = view.wire, view.component, view._repo
    await wire.render_diff(comp, repo)

    comp.note = "now set"
    diff = await wire.render_diff(comp, repo)

    assert diff is not None and "s" not in diff
    assert "now set" in diff.values()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rebuilt_html_matches_a_marker_free_render():
    view = await _live(LoopProbe, items=[{"name": "a", "qty": 1}, {"name": "b", "qty": 2}])
    wire, comp, repo = view.wire, view.component, view._repo
    await wire.render_diff(comp, repo)

    rebuilt = wire._last_rendered.to_html()

    assert "<!--$" not in rebuilt
    assert '<li class="">a x 1</li><li class="">b x 2</li>' in rebuilt


@pytest.mark.asyncio
@pytest.mark.unit
async def test_http_render_has_no_markers_in_attributes():
    view = await mount(LoopProbe, note="v", items=[{"name": "a", "qty": 1}])

    rendered = str(view.render())

    assert "<!--$" not in rendered
    assert '<li class="">a x 1</li>' in rendered
