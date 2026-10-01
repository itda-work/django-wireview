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
from wireview.core.rendered import PROTOCOL_VERSION, component_refs
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
    "sl/hostpage.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'SlFrame' id='frame' %}"
        "{% fill body %}SLOT-TEXT {{ this.n }}{% live_component 'SlLeaf' id='leaf' %}{% endfill %}"
        "{% endcomponent %}"
        "{% live_component_block 'SlBox' id='box' %}"
        "{% fill body %}BOX-TEXT{% for k in this.keys %}{% live_component 'SlLeaf' id=k %}{% endfor %}{% endfill %}"
        "{% endlive_component %}"
        "</main>"
    ),
    "sl/frame.html": (
        "{% load wireview %}<section {% tag_header %}><span>{{ this.clicks }}</span>{% render_slot 'body' %}</section>"
    ),
    "sl/box.html": (
        "{% load wireview %}<aside {% live_tag_header %}><span>{{ this.clicks }}</span>{% render_slot 'body' %}</aside>"
    ),
    "sl/leaf.html": "{% load wireview %}<b {% live_tag_header %}>{{ this.pokes }}</b>",
    "sl/showpage.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'SlShowFrame' id='sf' %}"
        "{% fill body %}A{% live_component 'SlLeaf' id='sl1' %}B{% live_component 'SlLeaf' id='sl2' %}"
        "C{% component 'SlPlain' id='g' %}Z{% endfill %}"
        "{% endcomponent %}"
        "</main>"
    ),
    "sl/showframe.html": (
        "{% load wireview %}<section {% tag_header %}><span>{{ this.clicks }}</span>"
        "{% if this.show %}{% render_slot 'body' %}{% endif %}</section>"
    ),
    "sl/plain.html": "{% load wireview %}<em {% tag_header %}>G{{ this.clicks }}</em>",
    "sl/strictpage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% live_component_block 'SlStrict' id='s' %}x{% endlive_component %}"
        "</main>"
    ),
}


class SlPage(Component):
    class Meta:
        template_name = "sl/page.html"

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
    class Meta:
        template_name = "sl/modal.html"

    open: bool = False
    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlListPage(Component):
    class Meta:
        template_name = "sl/listpage.html"


class SlList(LiveComponent):
    class Meta:
        template_name = "sl/list.html"

    items: list[str] = ["a", "b"]

    async def add(self):
        self.items = [*self.items, "c"]


class SlCardPage(Component):
    class Meta:
        template_name = "sl/cardpage.html"

    heading: str = "Card head"
    body: str = "card body"


class SlCard(Component):
    class Meta:
        template_name = "sl/card.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlHostPage(Component):
    class Meta:
        template_name = "sl/hostpage.html"

    n: int = 0
    keys: list[str] = ["bl1", "bl2"]

    async def bump(self):
        self.n += 1


class SlFrame(Component):
    class Meta:
        template_name = "sl/frame.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlBox(LiveComponent):
    class Meta:
        template_name = "sl/box.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlLeaf(LiveComponent):
    class Meta:
        template_name = "sl/leaf.html"

    pokes: int = 0
    joins: t.ClassVar[list[str]] = []

    async def joined(self):
        SlLeaf.joins.append(self.id)

    async def poke(self):
        self.pokes += 1


class SlShowPage(Component):
    class Meta:
        template_name = "sl/showpage.html"

    n: int = 0

    async def bump(self):
        self.n += 1


class SlShowFrame(Component):
    class Meta:
        template_name = "sl/showframe.html"

    clicks: int = 0
    show: bool = True

    async def click(self):
        self.clicks += 1

    async def toggle(self):
        self.show = not self.show

    async def boom(self):
        raise RuntimeError("the frame went wrong")


class SlPlain(Component):
    class Meta:
        template_name = "sl/plain.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlStrictPage(Component):
    class Meta:
        template_name = "sl/strictpage.html"


class SlStrict(LiveComponent):
    class Meta:
        template_name = "sl/modal.html"
        slots = {"header": {"required": True, "doc": "the header"}}


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


# --- a LiveComponent inside a slot ---------------------------------------------------------


def refs(diff: t.Any) -> list[str]:
    """Ids of the component references anywhere in a diff payload."""
    found: list[str] = []

    def walk(value: t.Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("c"), str):
                found.append(value["c"])
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(diff)
    return found


async def join_host() -> tuple[WireviewConsumer, FakeOutbound]:
    from wireview.core.state import sign_state

    SlLeaf.joins = []
    consumer, outbound = make_consumer()
    await join(consumer, "SlHostPage", id="host")
    await consumer.command_join("SlFrame", sign_state(consumer.repo.get("frame")))
    return consumer, outbound


async def test_a_component_block_keeps_a_live_component_of_its_slot_on_its_own_join():
    """The nested component's own render names the LiveComponent its slot holds."""
    consumer, outbound = await join_host()

    frame = outbound.renders()[-1]
    assert frame["id"] == "frame"
    assert refs(frame["diff"]) == ["leaf"]
    rendered = consumer.repo.get("frame").wire._last_rendered
    assert component_refs(rendered) == ["leaf"]
    assert "SLOT-TEXT 0<!--@wv:leaf-->" in rendered.to_html()


async def test_a_component_block_keeps_a_live_component_of_its_slot_on_its_own_event():
    consumer, outbound = await join_host()
    outbound.commands.clear()

    await consumer.command_user_event("frame", "click", {}, {})

    [frame] = outbound.renders()
    rendered = consumer.repo.get("frame").wire._last_rendered
    assert component_refs(rendered) == ["leaf"]
    assert "<span>1</span>" in rendered.to_html() and "SLOT-TEXT 0<!--@wv:leaf-->" in rendered.to_html()
    assert "children" not in frame, "the leaf is the host's: the frame's render does not settle it"


async def test_the_live_component_of_a_slot_stays_the_fillers():
    """Its parent is the component that filled the slot, and the slot's owner's renders run no hook on it."""
    consumer, outbound = await join_host()
    await consumer.command_user_event("frame", "click", {}, {})

    leaf = consumer.repo.get("leaf")
    assert leaf._parent_id == "host"
    assert SlLeaf.joins.count("leaf") == 1
    outbound.commands.clear()

    await consumer.command_user_event("host", "bump", {}, {})

    assert consumer.repo.get("leaf") is leaf, "the host still names it: no leaving(), no new instance"
    assert "SLOT-TEXT 1<!--@wv:leaf-->" in consumer.repo.get("host").wire._last_rendered.to_html()


async def test_a_live_component_block_keeps_the_live_components_of_its_slot():
    consumer, outbound = await join_host()

    first = outbound.renders()[0]
    assert refs(first["children"]["box"]) == ["bl1", "bl2"]
    assert {"bl1", "bl2"} <= set(first["children"])
    outbound.commands.clear()

    await consumer.command_user_event("box", "click", {}, {})

    outbound.renders()
    rendered = consumer.repo.get("box").wire._last_rendered
    assert component_refs(rendered) == ["bl1", "bl2"]
    html = rendered.to_html()
    assert "BOX-TEXT<!--@wv:bl1--><!--@wv:bl2-->" in html and "<span>1</span>" in html
    assert consumer.repo.get("bl1")._parent_id == "host"


async def test_a_dead_render_of_remembered_slots_inlines_their_live_component():
    consumer, _ = await join_host()
    frame = consumer.repo.get("frame")
    dead = ComponentRepository(is_live=False, user=AnonymousUser())
    dead.components["leaf"] = consumer.repo.get("leaf")

    html = str(frame.wire.render(frame, dead))

    assert 'SLOT-TEXT 0<b id="leaf"' in html and "<!--" not in html


# --- what is in a slot when its owner renders on its own -----------------------------------
#
# sl/showpage.html fills the frame's slot with text around and between two
# LiveComponents and a plain component. The frame renders that slot on its own
# join and events, and can hide it and show it again.


async def join_show_page() -> tuple[WireviewConsumer, FakeOutbound]:
    from wireview.core.state import sign_state

    SlLeaf.joins = []
    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlShowPage", id="host")
    await consumer.command_join("SlShowFrame", sign_state(consumer.repo.get("sf")))
    await consumer.command_join("SlPlain", sign_state(consumer.repo.get("g")))
    return consumer, outbound


def state_of(html: str, component_id: str) -> str:
    import re

    match = re.search(rf'id="{component_id}"[^>]*?data-state="([^"]+)"', html)
    assert match, f"no data-state for {component_id} in {html}"
    return match.group(1)


async def test_the_text_around_and_between_the_references_of_a_slot_stays():
    consumer, outbound = await join_show_page()
    outbound.commands.clear()

    await consumer.command_user_event("sf", "click", {}, {})

    html = child_html(consumer, "sf")
    assert html.startswith('<section id="sf"') and "<span>1</span>" in html
    assert 'A<!--@wv:sl1-->B<!--@wv:sl2-->C<em id="g"' in html
    assert html.endswith(">G0</em>Z</section>")


async def test_the_marks_around_a_nested_component_never_reach_the_page():
    """They are for the slot's owner, which keeps the fill as text; every render drops them when parsed."""
    consumer, outbound = await join_show_page()
    await consumer.command_user_event("sf", "click", {}, {})

    sent = json.dumps(outbound.renders())
    assert "@wv(" not in sent and "@wv)" not in sent
    for component_id in ("host", "sf", "g"):
        assert "@wv(" not in child_html(consumer, component_id)


async def test_a_plain_component_in_a_slot_shows_its_current_state_on_the_owners_render():
    """The slot's text holds the plain component as the host's pass drew it; the owner draws it as it is now."""
    consumer, outbound = await join_show_page()
    await consumer.command_user_event("g", "click", {}, {})
    g_html = child_html(consumer, "g")
    assert "G1" in g_html
    outbound.commands.clear()

    await consumer.command_user_event("sf", "click", {}, {})

    html = child_html(consumer, "sf")
    assert ">G1</em>Z" in html and "G0" not in html
    assert state_of(html, "g") == state_of(g_html, "g"), "the old data-state would join g back to its old state"


async def test_a_plain_component_that_left_is_not_drawn_by_the_slots_owner():
    consumer, outbound = await join_show_page()
    await consumer.command_leave("g")
    outbound.commands.clear()

    await consumer.command_user_event("sf", "click", {}, {})

    html = child_html(consumer, "sf")
    assert 'id="g"' not in html and "<!--@wv:sl2-->CZ</section>" in html


async def test_an_owner_that_shows_its_slot_again_sends_the_live_components_in_it():
    """The page dropped them with the hidden slot; the host still owns them, so nothing else brings them back."""
    consumer, outbound = await join_show_page()
    await consumer.command_user_event("sl1", "poke", {}, {})
    await consumer.command_user_event("sf", "toggle", {}, {})
    assert component_refs(consumer.repo.get("sf").wire._last_rendered) == []
    outbound.commands.clear()

    await consumer.command_user_event("sf", "toggle", {}, {})

    [render] = outbound.renders()
    assert refs(render["diff"]) == ["sl1", "sl2"]
    assert set(render["children"]) == {"sl1", "sl2"}
    assert "s" in render["children"]["sl1"], "a full render: the page has nothing to apply a partial one to"
    assert render["children"]["sl1"]["d"][-1] == "1", "the state it has now"
    assert "instances" not in render, "the same instances as before"
    assert SlLeaf.joins == ["sl1", "sl2"], "no lifecycle: the host still owns them"
    assert consumer.repo.get("sl1")._parent_id == "host"
    outbound.commands.clear()

    await consumer.command_user_event("sf", "click", {}, {})

    assert "children" not in outbound.renders()[0], "only a render that names them anew sends them"


async def test_an_owner_joined_again_after_its_handler_raised_keeps_its_slot():
    from wireview.core.state import sign_state

    consumer, outbound = await join_show_page()
    state = sign_state(consumer.repo.get("sf"))
    await consumer.command_user_event("sf", "boom", {}, {})
    assert ("error", {"id": "sf", "during": "event"}) in outbound.commands
    outbound.commands.clear()

    await consumer.command_join("SlShowFrame", state)

    html = child_html(consumer, "sf")
    assert "A<!--@wv:sl1-->B<!--@wv:sl2-->C<em" in html and ">G0</em>Z" in html


async def test_an_owner_joined_again_with_its_page_keeps_its_slot():
    """A boosted visit to a page with the same ids joins the host and then the frame again."""
    from wireview.core.state import sign_state

    consumer, outbound = await join_show_page()
    host_state = sign_state(consumer.repo.get("host"))
    frame_state = sign_state(consumer.repo.get("sf"))
    outbound.commands.clear()

    await consumer.command_join("SlShowPage", host_state)
    await consumer.command_join("SlShowFrame", frame_state)

    assert outbound.renders()[-1]["id"] == "sf"
    html = child_html(consumer, "sf")
    assert "A<!--@wv:sl1-->B<!--@wv:sl2-->C<em" in html and ">G0</em>Z" in html


async def test_a_new_instance_takes_no_slot_another_class_left_under_its_id():
    from wireview.core.state import sign_state

    consumer, outbound = await join_show_page()
    await consumer.command_user_event("sf", "boom", {}, {})
    stranger = consumer.repo.build("SlFrame", {"id": "sf"})
    consumer.repo.remove("sf")

    await consumer.command_join("SlFrame", sign_state(stranger))

    assert consumer.repo.get("sf").wire.slots is None


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
