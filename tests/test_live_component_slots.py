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
from wireview.core.rendered import LEAVES_FIRST_SINCE, PROTOCOL_VERSION, component_refs
from wireview.core.state import sign_state
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
    "sl/deeppage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'SlShowFrame' id='df' %}{% fill body %}D{% live_component 'SlNestLeaf' id='dl' %}"
        "{% endfill %}{% endcomponent %}</main>"
    ),
    "sl/nestleaf.html": (
        "{% load wireview %}<b {% live_tag_header %}>{{ this.pokes }}{% live_component 'SlLeaf' id='dl-inner' %}</b>"
    ),
    "sl/proppage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'SlShowFrame' id='pf' show=this.open %}"
        "{% fill body %}P{% live_component 'SlLeaf' id='plf' pokes=this.n %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "sl/rowspage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% for k in this.keys %}<p>{{ k }}{{ this.n }}{% live_component 'SlLeaf' id=k %}</p>{% endfor %}</main>"
    ),
    "sl/relaypage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'SlRelay' id='relay' %}{% fill body %}R{% component 'SlPlain' id='rg' %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "sl/relay.html": (
        "{% load wireview %}<div {% tag_header %}><span>{{ this.clicks }}</span>"
        "{% component_block 'SlShowFrame' id='rf' %}{% fill body %}{% render_slot 'body' %}{% endfill %}"
        "{% endcomponent %}</div>"
    ),
    "sl/holderpage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'SlShowFrame' id='hf' %}{% fill body %}S{% component 'SlHolder' id='gh' %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "sl/holder.html": "{% load wireview %}<em {% tag_header %}>H{% live_component 'SlLeaf' id='gl' %}</em>",
    "sl/listframepage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'SlListFrame' id='lf' %}{% fill body %}L{% component 'SlPlain' id='lg' %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "sl/listframe.html": (
        "{% load wireview %}<section {% tag_header %}>{% for i in this.items %}<i>{{ i }}</i>{% endfor %}"
        "{% render_slot 'body' %}<s>{{ this.items|length }}</s></section>"
    ),
    "sl/nestholderpage.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'SlShowFrame' id='nf' %}{% fill body %}N{% component 'SlNestHolder' id='nh' %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "sl/nestholder.html": "{% load wireview %}<em {% tag_header %}>{% component 'SlPlain' id='ng' %}</em>",
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

    async def boom(self):
        raise RuntimeError("the frame went wrong")


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

    async def boom(self):
        raise RuntimeError("the host went wrong")


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


class SlNestLeaf(LiveComponent):
    class Meta:
        template_name = "sl/nestleaf.html"

    pokes: int = 0


class SlDeepPage(Component):
    class Meta:
        template_name = "sl/deeppage.html"


class SlPropPage(Component):
    class Meta:
        template_name = "sl/proppage.html"

    n: int = 0
    open: bool = True

    async def flip(self):
        self.open = not self.open
        self.n += 1


class SlRowsPage(Component):
    class Meta:
        template_name = "sl/rowspage.html"

    keys: list[str] = ["r1", "r2", "r3"]
    n: int = 0

    async def bump(self):
        self.n += 1


class SlRelayPage(Component):
    class Meta:
        template_name = "sl/relaypage.html"


class SlRelay(Component):
    class Meta:
        template_name = "sl/relay.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlHolderPage(Component):
    class Meta:
        template_name = "sl/holderpage.html"


class SlHolder(Component):
    class Meta:
        template_name = "sl/holder.html"


class SlListFramePage(Component):
    class Meta:
        template_name = "sl/listframepage.html"


class SlListFrame(Component):
    class Meta:
        template_name = "sl/listframe.html"

    items: list[int] = [0]

    async def add(self):
        self.items = [*self.items, len(self.items)]

    async def drop(self):
        self.items = self.items[:-1]


class SlNestHolderPage(Component):
    class Meta:
        template_name = "sl/nestholderpage.html"


class SlNestHolder(Component):
    class Meta:
        template_name = "sl/nestholder.html"


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
    assert ">G1</em>Z" in html and ">G0</em>" not in html
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


# --- whose slot a component joined again takes ---------------------------------------------
#
# Slots are not in the signed state, so an instance retired for a join under its
# id hands them to the next one. Only when that join is the same element joining
# again: another page can render a component of the same class and id with no
# fill, and its slot is empty there.


def another_pages_frame() -> str:
    """The frame as another page renders it: same class and id, no fill."""
    from wireview.core.state import sign_state

    http = ComponentRepository(is_live=False, user=AnonymousUser())
    return sign_state(http.build("SlShowFrame", {"id": "sf"}))


@pytest.mark.parametrize("leave_first", [True, False], ids=["leave-first", "join-first"])
async def test_another_page_with_the_id_and_no_fill_draws_no_slot(leave_first):
    """A boosted visit to it joins the frame again; the slot the host gave the old one stays behind."""
    consumer, outbound = await join_show_page()
    outbound.commands.clear()

    if leave_first:
        await consumer.command_leave("host")
        await consumer.command_leave("g")
    await consumer.command_join("SlShowFrame", another_pages_frame())
    if not leave_first:
        await consumer.command_leave("host")
        await consumer.command_leave("g")

    [render] = [r for r in outbound.renders() if r["id"] == "sf"]
    assert refs(render["diff"]) == [] and "children" not in render
    assert child_html(consumer, "sf").endswith("<span>0</span></section>")
    assert consumer.repo.get("sf").wire.slots is None
    assert consumer.repo._slots_to_rejoin == {}


async def test_a_page_left_before_its_owner_joined_again_takes_the_slot_along():
    """The slot kept for the join answering an error goes with the page that filled it."""
    consumer, outbound = await join_show_page()
    await consumer.command_user_event("sf", "boom", {}, {})
    assert ("error", {"id": "sf", "during": "event"}) in outbound.commands
    outbound.commands.clear()

    await consumer.command_leave("host")
    await consumer.command_leave("g")

    assert consumer.repo._slots_to_rejoin == {}, "nothing kept for the rest of the connection"
    await consumer.command_join("SlShowFrame", another_pages_frame())
    assert child_html(consumer, "sf").endswith("<span>0</span></section>")


async def test_another_page_after_the_host_joined_again_draws_no_slot():
    """The host joined again after an error fills the frame in its pass; a page without the host has no fill."""
    from wireview.core.state import sign_state

    consumer, outbound = await join_show_page()
    host_state = sign_state(consumer.repo.get("host"))
    await consumer.command_user_event("host", "boom", {}, {})
    await consumer.command_join("SlShowPage", host_state)
    assert consumer.repo.get("sf").wire.slots_from is consumer.repo.get("host")
    outbound.commands.clear()

    await consumer.command_leave("host")
    await consumer.command_leave("g")
    await consumer.command_join("SlShowFrame", another_pages_frame())

    assert consumer.repo.get("sf").wire.slots is None
    assert child_html(consumer, "sf").endswith("<span>0</span></section>")


# Bundles before LEAVES_FIRST_SINCE (v1.0.0rc1-rc3) join the new page's elements
# before they send the old page's leaves, so a boosted visit cannot tell the
# same element from another page's: the host the old page still holds may have
# come after the frame. They get an empty slot on any boosted visit instead.


async def test_a_bundle_that_joins_first_takes_no_slot_from_the_page_before():
    consumer, outbound = await join_show_page()
    consumer.repo.vsn = LEAVES_FIRST_SINCE - 1
    host_state = sign_state(consumer.repo.get("host"))
    await consumer.command_user_event("host", "boom", {}, {})
    await consumer.command_join("SlShowPage", host_state)
    assert consumer.repo.get("host").wire.born > consumer.repo.get("sf").wire.born

    await consumer.command_join("SlShowFrame", another_pages_frame())
    await consumer.command_leave("host")
    await consumer.command_leave("g")

    assert consumer.repo.get("sf").wire.slots is None
    assert child_html(consumer, "sf").endswith("<span>0</span></section>")
    assert consumer.repo._slots_to_rejoin == {}


@pytest.mark.parametrize(
    ("vsn", "kept"), [(LEAVES_FIRST_SINCE, True), (LEAVES_FIRST_SINCE - 1, False)], ids=["leaves-first", "joins-first"]
)
async def test_a_boosted_visit_to_the_same_page_keeps_the_slot_for_a_bundle_that_leaves_first(vsn, kept):
    consumer, outbound = await join_show_page()
    consumer.repo.vsn = vsn
    host_state = sign_state(consumer.repo.get("host"))
    frame_state = sign_state(consumer.repo.get("sf"))

    await consumer.command_join("SlShowPage", host_state)
    await consumer.command_join("SlShowFrame", frame_state)

    assert ("A<!--@wv:sl1-->B" in child_html(consumer, "sf")) is kept
    assert (consumer.repo.get("sf").wire.slots is not None) is kept


async def test_a_bundle_that_joins_first_keeps_the_slot_of_an_owner_joined_again_after_an_error():
    """The join answering ``error`` is that element's whatever the order of leaves."""
    consumer, outbound = await join_show_page()
    consumer.repo.vsn = LEAVES_FIRST_SINCE - 1
    state = sign_state(consumer.repo.get("sf"))
    await consumer.command_user_event("sf", "boom", {}, {})

    await consumer.command_join("SlShowFrame", state)

    assert "A<!--@wv:sl1-->B<!--@wv:sl2-->C<em" in child_html(consumer, "sf")


async def test_an_owner_that_left_before_joining_again_leaves_nothing_kept():
    consumer, outbound = await join_show_page()
    await consumer.command_user_event("sf", "boom", {}, {})
    assert "sf" in consumer.repo._slots_to_rejoin

    await consumer.command_leave("sf")

    assert consumer.repo._slots_to_rejoin == {}


async def test_a_fill_the_host_rendered_since_the_error_wins_over_the_kept_one():
    from wireview.core.state import sign_state

    consumer, outbound = await join_host()
    consumer.repo.vsn = PROTOCOL_VERSION
    state = sign_state(consumer.repo.get("frame"))
    await consumer.command_user_event("frame", "boom", {}, {})
    await consumer.command_user_event("host", "bump", {}, {})
    outbound.commands.clear()

    await consumer.command_join("SlFrame", state)

    html = child_html(consumer, "frame")
    assert "SLOT-TEXT 1<!--@wv:leaf-->" in html and "SLOT-TEXT 0" not in html


async def test_a_page_with_the_ids_whose_host_joined_first_hands_the_slot_on():
    """The host's pass gave its fill to the old frame; the frame's own join takes it from there."""
    from wireview.core.state import sign_state

    consumer, outbound = await join_show_page()
    host_state = sign_state(consumer.repo.get("host"))
    frame_state = sign_state(consumer.repo.get("sf"))
    old_host = consumer.repo.get("host")

    await consumer.command_join("SlShowPage", host_state)
    assert consumer.repo.get("host") is not old_host
    await consumer.command_join("SlShowFrame", frame_state)

    assert consumer.repo.get("sf").wire.slots is not None
    assert consumer.repo._slots_to_rejoin == {}


# --- a LiveComponent shown again, and what else its render carries ------------------------


async def test_shown_again_carries_the_live_components_of_the_live_component_too():
    from wireview.core.state import sign_state

    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlDeepPage", id="host")
    await consumer.command_join("SlShowFrame", sign_state(consumer.repo.get("df")))
    await consumer.command_user_event("df", "toggle", {}, {})
    outbound.commands.clear()

    await consumer.command_user_event("df", "toggle", {}, {})

    [render] = outbound.renders()
    assert set(render["children"]) == {"dl", "dl-inner"}
    assert "s" in render["children"]["dl-inner"]


async def test_a_host_that_shows_a_slot_and_changes_its_live_component_sends_it_whole():
    """The prop change makes a partial diff of it; the page dropped it with the hidden slot."""
    from wireview.core.state import sign_state

    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlPropPage", id="host")
    await consumer.command_join("SlShowFrame", sign_state(consumer.repo.get("pf")))
    await consumer.command_user_event("host", "flip", {}, {})
    outbound.commands.clear()

    await consumer.command_user_event("host", "flip", {}, {})

    [render] = [r for r in outbound.renders() if r["id"] == "host"]
    child = render["children"]["plf"]
    assert "s" in child and child["d"][-1] == "2"


async def test_a_render_that_names_again_the_live_components_it_named_sends_none_of_them():
    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlRowsPage", id="rows")
    outbound.commands.clear()

    await consumer.command_user_event("rows", "bump", {}, {})

    [render] = outbound.renders()
    assert refs(render["diff"]) == ["r1", "r2", "r3"], "the rows changed carry their LiveComponents"
    assert "children" not in render, "the page still holds them"


# --- a slot passed on ----------------------------------------------------------------------


async def join_relay() -> tuple[WireviewConsumer, FakeOutbound]:
    from wireview.core.state import sign_state

    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlRelayPage", id="host")
    for name, component_id in (("SlRelay", "relay"), ("SlShowFrame", "rf"), ("SlPlain", "rg")):
        await consumer.command_join(name, sign_state(consumer.repo.get(component_id)))
    return consumer, outbound


async def test_a_slot_passed_on_draws_its_plain_component_as_it_is_now():
    consumer, outbound = await join_relay()
    await consumer.command_user_event("relay", "click", {}, {})
    await consumer.command_user_event("rg", "click", {}, {})
    await consumer.command_user_event("rg", "click", {}, {})

    await consumer.command_user_event("rf", "click", {}, {})

    html = child_html(consumer, "rf")
    assert ">G2</em>" in html and ">G0</em>" not in html
    assert "@wv(" not in json.dumps(outbound.renders())


async def test_a_component_passing_a_slot_on_draws_its_plain_component_as_it_is_now():
    consumer, outbound = await join_relay()
    await consumer.command_user_event("rg", "click", {}, {})

    await consumer.command_user_event("relay", "click", {}, {})

    assert ">G1</em>" in child_html(consumer, "relay")


# --- a plain component in a slot that did not change ---------------------------------------


async def test_the_owners_render_reuses_a_plain_component_that_has_not_rendered_since(monkeypatch):
    """What the owner drew last is current until the component renders on its own; drawing it costs a render."""
    consumer, outbound = await join_show_page()
    drawn: list[str] = []
    original = SlPlain._render

    def counting(self, repo):
        drawn.append(self.id)
        return original(self, repo)

    monkeypatch.setattr(SlPlain, "_render", counting)

    await consumer.command_user_event("sf", "click", {}, {})
    assert drawn == ["g"], "g's join rendered it after the host's pass drew it"
    await consumer.command_user_event("sf", "click", {}, {})
    await consumer.command_user_event("sf", "click", {}, {})
    assert drawn == ["g"]
    assert ">G0</em>Z" in child_html(consumer, "sf")

    await consumer.command_user_event("g", "click", {}, {})
    await consumer.command_user_event("sf", "click", {}, {})
    assert drawn == ["g", "g"]
    assert ">G1</em>Z" in child_html(consumer, "sf")


async def test_a_plain_component_holding_a_live_component_keeps_it_on_the_owners_renders():
    """Its text names the LiveComponent: put back as it is, the reference would be a bare comment."""
    from wireview.core.state import sign_state

    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlHolderPage", id="host")
    await consumer.command_join("SlShowFrame", sign_state(consumer.repo.get("hf")))
    await consumer.command_join("SlHolder", sign_state(consumer.repo.get("gh")))
    gl = consumer.repo.get("gl")

    for _ in range(2):
        await consumer.command_user_event("hf", "click", {}, {})
        assert component_refs(consumer.repo.get("hf").wire._last_rendered) == ["gl"]
    assert consumer.repo.get("gl") is gl


async def test_a_plain_component_holding_a_plain_component_draws_it_as_it_is_now():
    """What the holder drew last holds the inner one as it was: the inner one renders on its own."""
    from wireview.core.state import sign_state

    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlNestHolderPage", id="host")
    for name, component_id in (("SlShowFrame", "nf"), ("SlNestHolder", "nh"), ("SlPlain", "ng")):
        await consumer.command_join(name, sign_state(consumer.repo.get(component_id)))
    await consumer.command_user_event("nf", "click", {}, {})

    await consumer.command_user_event("ng", "click", {}, {})
    await consumer.command_user_event("nf", "click", {}, {})

    assert ">G1</em></em></section>" in child_html(consumer, "nf")


async def run_list_frame(reuse: bool, monkeypatch) -> list[t.Any]:
    """Diffs and HTML of the owner's renders, its markers before the slot changing in number."""
    import re

    from wireview.core.state import sign_state
    from wireview.slots import _NestedComponentNode

    if not reuse:
        render = _NestedComponentNode.render

        def fresh(self, context):
            self._drawn = None
            return render(self, context)

        monkeypatch.setattr(_NestedComponentNode, "render", fresh)
    from wireview.core.rendered import Rendered

    raw: list[str] = []
    parse = Rendered.from_marked_html.__func__  # type: ignore[attr-defined]

    def recording(cls, html, stale=()):
        if 'id="lf"' in html[:20]:
            raw.append(html)
        return parse(cls, html, stale)

    monkeypatch.setattr(Rendered, "from_marked_html", classmethod(recording))
    consumer, outbound = make_consumer()
    consumer.repo.vsn = PROTOCOL_VERSION
    await join(consumer, "SlListFramePage", id="host")
    await consumer.command_join("SlListFrame", sign_state(consumer.repo.get("lf")))
    await consumer.command_join("SlPlain", sign_state(consumer.repo.get("lg")))
    outbound.commands.clear()
    seen: list[t.Any] = []
    for event in ("add", "add", "drop", "add", "drop", "drop"):
        await consumer.command_user_event("lf", event, {}, {})
        seen.append(child_html(consumer, "lf"))
    seen.extend(outbound.renders())
    seen.extend(raw)
    token = re.compile(r"ey[\w-]+:[\w-]+:[\w-]+")

    def masked(value: t.Any) -> t.Any:
        if isinstance(value, str):
            return token.sub("STATE", value)
        if isinstance(value, list):
            return [masked(v) for v in value]
        if isinstance(value, dict):
            return {k: masked(v) for k, v in value.items()}
        return value

    return masked(seen)


async def test_a_plain_component_put_back_renders_as_drawing_it_again_would(monkeypatch):
    reused = await run_list_frame(True, monkeypatch)
    drawn = await run_list_frame(False, monkeypatch)

    assert reused == drawn, "the same marked output, numbered for where it is now"
    assert ">G0</em><s>1</s></section>" in reused[5]


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
