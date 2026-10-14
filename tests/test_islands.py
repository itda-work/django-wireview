"""``{% island %}``: an element the page's own JavaScript draws, fed the component's state (#221).

The tag renders the hook element the client mounts the island on, the props as
JSON in an attribute, and the inner element no render touches. The props are
one dynamic part: a render that changes them sends that JSON, not the island's
markup (``tests/test_islands_e2e.py`` sees the client hand them over).
"""

from __future__ import annotations

import datetime
import decimal
import html
import json
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template, TemplateSyntaxError
from django.test import override_settings
from testproj.outbound import RecordingOutbound

from wireview import Component
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.session import WireviewSession

TEMPLATES = {
    "islands/map.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ this.zoom }}</b>"
        '{% island "Map" props=this.map_props class="h-96" data_role="map" %}</div>'
    ),
}


class IslandMap(Component):
    class Meta:
        template_name = "islands/map.html"

    zoom: int = 3

    @property
    def map_props(self) -> dict[str, t.Any]:
        return {"zoom": self.zoom, "label": '<"a" & b>'}

    async def zoom_in(self):
        self.zoom += 1


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


def render(source: str, **context: t.Any) -> str:
    return Template("{% load wireview %}" + source).render(Context(context))


def props_of(markup: str) -> t.Any:
    start = markup.index('wire-island-props="') + len('wire-island-props="')
    return json.loads(html.unescape(markup[start : markup.index('"', start)]))


# --- the markup -----------------------------------------------------------------------


@pytest.mark.unit
def test_the_island_is_a_hook_element_around_an_ignored_one():
    markup = render('{% island "Map" props=props %}', props={"zoom": 3})

    assert markup == (
        '<div wire-hook="wireview-island" wire-island="Map" wire-island-props="{&quot;zoom&quot;: 3}">'
        '<div wire-update="ignore"></div></div>'
    )


@pytest.mark.unit
def test_the_props_survive_the_attribute_whatever_they_hold():
    props = {"label": "<\"a\" & 'b'>", "nested": [1, None, True], "text": "</div><script>"}
    markup = render('{% island "Map" props=props %}', props=props)

    assert props_of(markup) == props
    assert "<script>" not in markup


@pytest.mark.unit
def test_dates_and_decimals_are_encoded_as_django_does():
    when = datetime.date(2026, 10, 14)
    markup = render('{% island "Map" props=props %}', props={"when": when, "price": decimal.Decimal("1.50")})

    assert props_of(markup) == {"when": "2026-10-14", "price": "1.50"}


@pytest.mark.unit
@pytest.mark.parametrize("source", ['{% island "Map" %}', '{% island "Map" props=nothing %}'])
def test_no_props_are_an_empty_object(source):
    assert props_of(render(source, nothing=None)) == {}


@pytest.mark.unit
def test_other_keywords_are_attributes_of_the_outer_element_escaped():
    markup = render('{% island name props=p class=cls data_role="map" %}', name="Chart", p={}, cls='a" onclick="x')

    assert markup.startswith(
        '<div wire-hook="wireview-island" wire-island="Chart" class="a&quot; onclick=&quot;x" data-role="map" '
    )


@pytest.mark.unit
def test_props_that_are_not_json_say_so():
    with pytest.raises(TypeError, match="island %} props must be JSON"):
        render('{% island "Map" props=props %}', props={"x": object()})


@pytest.mark.unit
@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_a_number_json_cannot_hold_is_refused_here_not_in_the_browser(number):
    """``json.dumps`` writes NaN and Infinity; the page's ``JSON.parse`` refuses them."""
    with pytest.raises(ValueError, match="island %} props must be JSON"):
        render('{% island "Map" props=props %}', props={"series": [1.0, number]})


@pytest.mark.unit
@pytest.mark.parametrize(
    "source, message",
    [
        ("{% island %}", "names the island"),
        ('{% island "Map" "extra" %}', "keyword arguments only"),
    ],
)
def test_a_malformed_tag_does_not_compile(source, message):
    with pytest.raises(TemplateSyntaxError, match=message):
        render(source)


@pytest.mark.unit
def test_a_name_that_is_not_a_string_is_refused():
    with pytest.raises(TemplateSyntaxError, match="with a string"):
        render("{% island name %}", name=None)


# --- the diff ---------------------------------------------------------------------------


async def joined() -> tuple[WireviewSession, RecordingOutbound]:
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, user=AnonymousUser(), channel_name="island-1")
    await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    state = sign_state(IslandMap(id="map", user=AnonymousUser(), wire=WireviewMeta(params={})))
    await session.handle_message({"command": "join", "payload": {"name": "IslandMap", "state": state}})
    return session, outbound


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_a_render_that_changes_the_props_sends_their_json_and_not_the_island():
    session, outbound = await joined()
    (first,) = outbound.renders()
    assert any("wire-island=" in part for part in first["diff"]["s"])
    outbound.commands.clear()

    event = {"id": "map", "command": "zoom_in", "implicit_args": {}, "explicit_args": {}}
    await session.handle_message({"command": "user_event", "payload": event})

    (render_,) = outbound.renders()
    diff = render_["diff"]
    assert "s" not in diff, "a full render"
    values = [value for value in diff.values() if isinstance(value, str)]
    assert not any("wire-island" in value for value in values)
    (props,) = [value for value in values if "zoom" in value]
    assert json.loads(html.unescape(props)) == {"zoom": 4, "label": '<"a" & b>'}
