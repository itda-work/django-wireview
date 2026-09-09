"""Slots for LiveComponents: ``{% live_component_block %}`` (GAP-036, #82).

Slot content rendered in the parent's pass reaches the child's own render, without the
parent's diff markers, and a change in that content re-renders the child. A component that
re-renders on its own event keeps its slots too, whether it is a LiveComponent or a nested
Component rendered with ``{% component_block %}``.
"""

import json
import typing as t

import pytest
from django import template
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "sl/page.html": (
        "{% load wireview %}<main {% tag_header %}><h1>{{ this.title }}</h1>"
        "{% live_component_block 'SlModal' id='m' open=this.open %}"
        "{% fill header %}<b>{{ this.heading }}</b>{% endfill %}"
        "<p>{{ this.body }}</p>"
        "{% endlive_component %}"
        "</main>"
    ),
    "sl/modal.html": (
        "{% load wireview %}<div {% live_tag_header %}>"
        "{% if slots.header %}<header>{% render_slot 'header' %}</header>{% endif %}"
        '<div class="body">{% render_slot %}</div><i>{{ this.open }}</i><span>{{ this.clicks }}</span>'
        "</div>"
    ),
    "sl/listpage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% live_component_block 'SlList' id='l' %}"
        "{% fill item let:item let:index %}<li>{{ index }}:{{ item }}</li>{% endfill %}"
        "{% endlive_component %}"
        "</main>"
    ),
    "sl/list.html": (
        "{% load wireview %}<ul {% live_tag_header %}>"
        "{% for it in this.items %}{% render_slot 'item' item=it index=forloop.counter %}{% endfor %}"
        "</ul>"
    ),
    "sl/cardpage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'SlCard' id='card' %}{% fill header %}<b>{{ this.heading }}</b>{% endfill %}"
        "<p>{{ this.body }}</p>{% endcomponent %}"
        "</main>"
    ),
    "sl/card.html": (
        "{% load wireview %}<section {% tag_header %}><header>{% render_slot 'header' %}</header>"
        "<div>{% render_slot %}</div><span>{{ this.clicks }}</span></section>"
    ),
    "sl/strictpage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% live_component_block 'SlStrict' id='s' %}x{% endlive_component %}"
        "</main>"
    ),
}


class SlPage(Component):
    _template_name = "sl/page.html"
    title: str = "Page"
    heading: str = "Hello"
    body: str = "body text"
    open: bool = False

    async def retitle(self):
        self.title = "Page!"

    async def rehead(self):
        self.heading = "Changed"

    async def toggle(self):
        self.open = not self.open


class SlModal(LiveComponent):
    _template_name = "sl/modal.html"
    open: bool = False
    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlListPage(Component):
    _template_name = "sl/listpage.html"


class SlList(LiveComponent):
    _template_name = "sl/list.html"
    items: list[str] = ["a", "b"]

    async def add(self):
        self.items = [*self.items, "c"]


class SlCardPage(Component):
    _template_name = "sl/cardpage.html"
    heading: str = "Card head"
    body: str = "card body"


class SlCard(Component):
    _template_name = "sl/card.html"
    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlStrictPage(Component):
    _template_name = "sl/strictpage.html"


class SlStrict(LiveComponent):
    _template_name = "sl/modal.html"
    _slots = {"header": {"required": True, "doc": "the header"}}


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...

    def renders(self) -> list[dict[str, t.Any]]:
        return [payload for command, payload in self.commands if command == "render"]


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


def make_consumer() -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


async def join(consumer: WireviewConsumer, name: str, **state) -> Component:
    component = await consumer.repo.join(name, {**state})
    await consumer.send_render(component)
    return component


def child_html(consumer: WireviewConsumer, child_id: str) -> str:
    return consumer.repo.get(child_id).wire._last_rendered.to_html()


# --- content reaches the child, not the parent -----------------------------------------------


async def test_slot_content_renders_inside_the_child_and_stays_out_of_the_parent_diff():
    consumer, outbound = make_consumer()
    await join(consumer, "SlPage", id="p")

    frame = outbound.renders()[0]
    parent_json = json.dumps(frame["diff"])
    assert "<b>Hello</b>" not in parent_json and '"c": "m"' in parent_json
    assert "s" in frame["children"]["m"], "the child's first render travels in the parent's frame"
    modal = child_html(consumer, "m")
    assert "<header><b>Hello</b></header>" in modal
    assert '<div class="body"><p>body text</p></div>' in modal


async def test_pre_rendered_fills_carry_no_markers_from_the_parents_pass():
    consumer, _ = make_consumer()
    await join(consumer, "SlPage", id="p")

    rendered = consumer.repo.get("m").wire._last_rendered
    assert "<!--$" not in "".join(rendered.static)
    assert all("<!--$" not in v for v in rendered.dynamic if isinstance(v, str))


async def test_a_let_fill_renders_with_the_values_render_slot_passes():
    consumer, outbound = make_consumer()
    await join(consumer, "SlListPage", id="lp")

    assert "<li>1:a</li><li>2:b</li>" in child_html(consumer, "l")
    outbound.commands.clear()

    await consumer.command_user_event("l", "add", {}, {})

    assert "<li>3:c</li>" in child_html(consumer, "l")


# --- the child keeps its slots across its own renders ---------------------------------------


async def test_the_childs_own_event_keeps_the_slot_content():
    consumer, outbound = make_consumer()
    await join(consumer, "SlPage", id="p")
    outbound.commands.clear()

    await consumer.command_user_event("m", "click", {}, {})

    assert [r["id"] for r in outbound.renders()] == ["m"]
    html = child_html(consumer, "m")
    assert "<header><b>Hello</b></header>" in html and "<span>1</span>" in html


async def test_a_nested_component_block_keeps_its_slots_on_its_own_event():
    """The same fix covers {% component_block %}: render_diff used to drop slots (#82 AC4)."""
    consumer, outbound = make_consumer()
    await join(consumer, "SlCardPage", id="cp")
    await consumer.command_join(
        "SlCard",
        __import__("wireview.core.state", fromlist=["sign_state"]).sign_state(consumer.repo.get("card")),
    )
    outbound.commands.clear()

    await consumer.command_user_event("card", "click", {}, {})

    html = child_html(consumer, "card")
    assert "<header><b>Card head</b></header>" in html and "<span>1</span>" in html
    assert "<div><p>card body</p></div>" in html


# --- slot changes drive the child's re-render ---------------------------------------------


async def test_changed_slot_content_rerenders_the_child_without_calling_update():
    consumer, outbound = make_consumer()
    await join(consumer, "SlPage", id="p")
    outbound.commands.clear()

    await consumer.command_user_event("p", "rehead", {}, {})

    frame = outbound.renders()[0]
    assert "m" in frame["children"]
    assert "<header><b>Changed</b></header>" in child_html(consumer, "m")


async def test_unchanged_slot_content_leaves_the_child_alone():
    consumer, outbound = make_consumer()
    await join(consumer, "SlPage", id="p")
    outbound.commands.clear()

    await consumer.command_user_event("p", "retitle", {}, {})

    assert "children" not in outbound.renders()[0]


async def test_a_changed_prop_and_changed_slots_render_the_child_once():
    consumer, outbound = make_consumer()
    await join(consumer, "SlPage", id="p")
    outbound.commands.clear()
    page = consumer.repo.get("p")
    page.heading = "Both"

    await consumer.command_user_event("p", "toggle", {}, {})

    frame = outbound.renders()[0]
    assert list(frame["children"]) == ["m"]
    html = child_html(consumer, "m")
    assert "<b>Both</b>" in html and "<i>True</i>" in html


# --- HTTP render and validation ------------------------------------------------------------


async def test_the_http_render_inlines_the_child_with_its_slots():
    repo = ComponentRepository(is_live=False, user=AnonymousUser())
    page = repo.build("SlPage", {"id": "p"})

    html = str(page._render(repo))

    assert "<header><b>Hello</b></header>" in html
    assert '<div class="body"><p>body text</p></div>' in html
    assert "<!--" not in html


async def test_a_required_slot_is_enforced_for_the_block_tag():
    consumer, _ = make_consumer()
    page = await consumer.repo.join("SlStrictPage", {"id": "sp"})

    with pytest.raises(template.TemplateSyntaxError, match="requires slot 'header'"):
        await page._render_diff(consumer.repo)
