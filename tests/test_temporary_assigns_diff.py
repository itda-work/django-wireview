"""A reset temporary assign is not a change (#111).

``Meta.temporary_assigns`` fields go back to their default after each render.
The page still shows what that render drew, so the next render -- whatever it
is for -- must not send the default in its place. Phoenix does not count the
reset as a change; before #111 the next render here sent the emptied list and
the list vanished from the page.

Each case below pairs the part that must stay with a control that must
change, so "nothing was sent" cannot pass for "everything was dropped".

Through the consumer (``command_user_event``), the way a browser's event renders.
"""

import json
import re
import typing as t
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component, LiveComponent, function_component
from wireview.consumer import WireviewConsumer
from wireview.core.rendered import page_drawing
from wireview.core.state import sign_state, state_of, unsign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "ta/this.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ this.count }}</b>"
        "<ul>{% for m in this.messages %}<li>{{ m }}</li>{% endfor %}</ul></div>"
    ),
    "ta/name.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul></div>"
    ),
    "ta/derived.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "<i>{{ message_count }}</i><s>{{ this.message_count }}</s><u>{{ messages|length }}</u></div>"
    ),
    "ta/block.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% if show %}<b>{{ count }}</b><ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>{% endif %}"
        "{% if messages %}<p>has messages</p>{% else %}<p>none</p>{% endif %}</div>"
    ),
    "ta/guarded.html": (
        "{% load wireview %}<div {% tag_header %}>{% if messages %}<b>{{ count }}</b>{% endif %}"
        "<i>{{ count }}</i></div>"
    ),
    "ta/nested.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "<ol>{% for r in rows %}<li>{{ r }}{% if messages %}:{{ messages|length }}{% endif %}</li>{% endfor %}"
        "</ol></div>"
    ),
    "ta/branch.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% if count %}<p>{{ messages|length }}</p>{% else %}<q>{{ messages|length }}</q>{% endif %}</div>"
    ),
    "ta/host.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component this.child id='g' count=this.n %}</main>"
    ),
    "ta/stalehost.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component this.child id='g' %}<ol>{% for m in this.notes %}<li>{{ m }}</li>{% endfor %}</ol></main>"
    ),
    "ta/slothost.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'TaFrame' id='f' %}{% fill body %}{% component this.child id='g' %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/frame.html": (
        "{% load wireview %}<section {% tag_header %}><s>{{ this.clicks }}</s>{% render_slot 'body' %}</section>"
    ),
    "ta/loops.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "<ol>{% for r in rows %}<li>{{ r }}{% if messages %}:{{ messages|length }}{% endif %}</li>{% endfor %}"
        "</ol></div>"
    ),
    "ta/slotted.html": (
        "{% load wireview %}<div {% tag_header %}>{% render_slot 'body' %}"
        "<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul></div>"
    ),
    "ta/blockhost.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotted' id='g' %}{% fill body %}<em>{{ this.title }}</em>{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/changingfill.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotted' id='g' %}{% fill body %}<em>{{ this.n }}</em>{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/livefill.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotted' id='g' %}{% fill body %}{% live_component 'TaLive' id='lc' %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/componentfill.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotted' id='g' %}{% fill body %}<em>{{ this.title }}</em>{% component 'TaK' id='k' %}"
        "{% endfill %}{% endcomponent %}</main>"
    ),
    "ta/k.html": "{% load wireview %}<p {% tag_header %}>K{{ k }}</p>",
    "ta/withlive.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% live_component 'TaLive' id='lc' %}{% endif %}</div>"
    ),
    "ta/live.html": "{% load wireview %}<span {% live_tag_header %}>live</span>",
    # What a stale block or loop holds that is not the component's own names (R10)
    "ta/livelist.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% for m in messages %}{% live_component 'TaLive' id=m %}{% endfor %}</div>"
    ),
    "ta/withk.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component 'TaK' id='k' %}{% endif %}</div>"
    ),
    "ta/withkblock.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component_block 'TaK' id='k' %}{% endcomponent %}{% endif %}</div>"
    ),
    # The nested component's joined() changes what it draws: a signed field, its own temporary assign
    "ta/withjoinedk.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component 'TaJoinedK' id='k' %}{% endif %}</div>"
    ),
    "ta/withloadedk.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component 'TaLoadedK' id='k' %}{% endif %}</div>"
    ),
    "ta/loadedk.html": "{% load wireview %}<p {% tag_header %}>K{{ k }}<s>{{ notes|length }}</s></p>",
    "ta/withhiddenk.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component 'TaHiddenK' id='k' %}{% endif %}</div>"
    ),
    "ta/hiddenk.html": "{% load wireview %}<p {% tag_header %}>K</p>",
    # joined() changes what it passes a grandchild that does not show it
    "ta/withg.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component 'TaG' id='g' %}{% endif %}</div>"
    ),
    "ta/g.html": "{% load wireview %}<p {% tag_header %}>G{% component 'TaH' id='h' size=notes|length %}</p>",
    "ta/h.html": "{% load wireview %}<i {% tag_header %}>H</i>",
    "ta/klist.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% for m in messages %}{% component 'TaK' id='k' %}{% endfor %}</div>"
    ),
    "ta/slotblock.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% render_slot 'body' %}{% endif %}</div>"
    ),
    "ta/slotblockhost.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotBlock' id='g' %}{% fill body %}<em>{{ this.n }}</em>{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/include.html": "{% load wireview %}<div {% tag_header %}>{% include 'ta/list.html' %}<b>{{ count }}</b></div>",
    "ta/includeother.html": (
        "{% load wireview %}<div {% tag_header %}>{% include 'ta/guardedlist.html' %}<b>{{ count }}</b></div>"
    ),
    "ta/blockinclude.html": (
        "{% load wireview %}<div {% tag_header %}>{% if messages %}{% include './list.html' %}{% endif %}"
        "{% if messages %}{% include 'ta/count.html' %}{% endif %}<b>{{ count }}</b></div>"
    ),
    "ta/list.html": "<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>",
    "ta/guardedlist.html": (
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul><i>{{ count }}</i>{% endif %}"
    ),
    "ta/count.html": "<s>{{ count }}</s>",
    "ta/stalefill.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotted' id='g' %}{% fill body %}<em>{{ notes|length }}</em>{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/stalelivefill.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% live_component_block 'TaLiveSlotted' id='lc' %}{% fill body %}<em>{{ notes|length }}</em>{% endfill %}"
        "{% endlive_component %}</main>"
    ),
    "ta/liveslotted.html": "{% load wireview %}<span {% live_tag_header %}>{% render_slot 'body' %}</span>",
    # A list whose rows a nested component draws, with ids and without
    "ta/rows.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "<ul>{% for m in messages %}{% component 'TaRow' id=m text=m %}{% endfor %}</ul></div>"
    ),
    "ta/rowsnoid.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "<ul>{% for m in messages %}{% component 'TaRow' text=m %}{% endfor %}</ul></div>"
    ),
    "ta/rowsfunc.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% func 'ta_k' %}{% endif %}</div>"
    ),
    "ta/func_k.html": "{% load wireview %}{% component 'TaK' id='k' %}",
    "ta/row.html": "{% load wireview %}<li {% tag_header %}>{{ text }}</li>",
    "ta/constslothost.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotBlock' id='g' %}{% fill body %}<em>static</em>{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/defaultslothost.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaDefaultSlotted' id='g' %}<em>{{ notes|length }}</em>{% endcomponent %}</main>"
    ),
    "ta/defaultslotted.html": "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>{% render_slot %}</div>",
    "ta/namedinclude.html": (
        "{% load wireview %}<div {% tag_header %}>{% with tpl='ta/count.html' %}"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>{% include tpl %}{% endif %}"
        "{% endwith %}<b>{{ count }}</b></div>"
    ),
    "ta/kslothost.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaSlotBlock' id='g' %}{% fill body %}{% component 'TaK' id='k' %}{% endfill %}"
        "{% endcomponent %}</main>"
    ),
    "ta/defaultslotblock.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% render_slot %}{% endif %}</div>"
    ),
    "ta/constdefaultslothost.html": (
        "{% load wireview %}<main {% tag_header %}><i>{{ this.n }}</i>"
        "{% component_block 'TaDefaultSlotBlock' id='g' %}<em>static</em>{% endcomponent %}</main>"
    ),
    # A nested component whose drawing moves outside its signed state (I6)
    "ta/notes.html": "{% load wireview %}<p {% tag_header %}>P<i>{{ notes|length }}</i></p>",
    "ta/withnotes.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component 'TaNotes' id='k' %}{% endif %}</div>"
    ),
    "ta/label.html": "{% load wireview %}<p {% tag_header %}>E<i>{{ label }}</i></p>",
    "ta/withlabel.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% component 'TaLabel' id='k' %}{% endif %}</div>"
    ),
    # Two temporary assigns: the block reads notes, the loop inside it messages
    "ta/two.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ count }}</b>"
        "{% if notes %}<s>{{ notes|length }}</s>"
        "{% for m in messages %}{% component 'TaK' id='k' %}{% endfor %}{% endif %}</div>"
    ),
    "ta/missinginclude.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>"
        "{% if never %}{% include 'ta/missing.html' %}{% endif %}{% endif %}<b>{{ count }}</b></div>"
    ),
}


class TaBase(Component):
    class Meta:
        temporary_assigns = {"messages"}

    count: int = 0
    show: bool = True
    rows: list[str] = ["a", "b"]
    messages: list[str] = []

    @property
    def message_count(self) -> int:
        return len(self.messages)

    async def load(self):
        self.messages = ["one", "two"]

    async def load_other(self):
        self.messages = ["three"]

    async def empty(self):
        self.messages = []

    async def append(self):
        self.messages.append("late")

    async def bump(self):
        self.count += 1

    async def add_row(self):
        self.rows = [*self.rows, "c"]


class TaThis(TaBase):
    class Meta:
        template_name = "ta/this.html"


class TaName(TaBase):
    class Meta:
        template_name = "ta/name.html"


class TaDerived(TaBase):
    class Meta:
        template_name = "ta/derived.html"


class TaBlock(TaBase):
    class Meta:
        template_name = "ta/block.html"


class TaGuarded(TaBase):
    class Meta:
        template_name = "ta/guarded.html"


class TaNested(TaBase):
    class Meta:
        template_name = "ta/nested.html"


class TaBranch(TaBase):
    class Meta:
        template_name = "ta/branch.html"


class TaLoops(TaBase):
    class Meta:
        template_name = "ta/loops.html"


class TaSlotted(TaBase):
    class Meta:
        template_name = "ta/slotted.html"


class TaWithLive(TaBase):
    class Meta:
        template_name = "ta/withlive.html"


class TaLive(LiveComponent):
    class Meta:
        template_name = "ta/live.html"


class TaLiveList(TaBase):
    class Meta:
        template_name = "ta/livelist.html"


class TaWithK(TaBase):
    class Meta:
        template_name = "ta/withk.html"


class TaWithKBlock(TaBase):
    class Meta:
        template_name = "ta/withkblock.html"


class TaWithJoinedK(TaBase):
    class Meta:
        template_name = "ta/withjoinedk.html"


class TaWithLoadedK(TaBase):
    class Meta:
        template_name = "ta/withloadedk.html"


class TaWithHiddenK(TaBase):
    class Meta:
        template_name = "ta/withhiddenk.html"


class TaWithG(TaBase):
    class Meta:
        template_name = "ta/withg.html"


class TaKList(TaBase):
    class Meta:
        template_name = "ta/klist.html"


class TaSlotBlock(TaBase):
    class Meta:
        template_name = "ta/slotblock.html"


class TaInclude(TaBase):
    class Meta:
        template_name = "ta/include.html"


class TaIncludeOther(TaBase):
    class Meta:
        template_name = "ta/includeother.html"


class TaBlockInclude(TaBase):
    class Meta:
        template_name = "ta/blockinclude.html"


class TaRows(TaBase):
    class Meta:
        template_name = "ta/rows.html"


class TaRowsNoId(TaBase):
    class Meta:
        template_name = "ta/rowsnoid.html"


class TaRowsFunc(TaBase):
    class Meta:
        template_name = "ta/rowsfunc.html"


@function_component(template="ta/func_k.html")
def ta_k() -> dict[str, t.Any]:
    return {}


class TaRow(Component):
    class Meta:
        template_name = "ta/row.html"

    text: str = ""

    async def shout(self):
        self.text = self.text.upper()


class TaDefaultSlotted(TaBase):
    class Meta:
        template_name = "ta/defaultslotted.html"


class TaNamedInclude(TaBase):
    class Meta:
        template_name = "ta/namedinclude.html"


class TaMissingInclude(TaBase):
    class Meta:
        template_name = "ta/missinginclude.html"


class TaDefaultSlotBlock(TaBase):
    class Meta:
        template_name = "ta/defaultslotblock.html"


class TaNotes(Component):
    class Meta:
        template_name = "ta/notes.html"
        temporary_assigns = {"notes"}

    notes: list[str] = []

    async def note(self):
        self.notes = ["x", "y"]


class TaWithNotes(TaBase):
    class Meta:
        template_name = "ta/withnotes.html"


class TaLabel(Component):
    class Meta:
        template_name = "ta/label.html"
        exclude_fields = {"label"}

    label: str = "a"

    async def relabel(self):
        self.label = "b"


class TaWithLabel(TaBase):
    class Meta:
        template_name = "ta/withlabel.html"


class TaTwo(TaBase):
    class Meta:
        template_name = "ta/two.html"
        temporary_assigns = {"messages", "notes"}

    notes: list[str] = []

    async def load(self):
        self.messages = ["one"]
        self.notes = ["n"]

    async def note(self):
        self.notes = ["n", "m"]


class TaLiveSlotted(LiveComponent):
    class Meta:
        template_name = "ta/liveslotted.html"


class TaHost(Component):
    """Draws a component with temporary assigns in its own pass, passing it ``count``."""

    class Meta:
        template_name = "ta/host.html"

    child: str = "TaThis"
    n: int = 0

    async def bump(self):
        self.n += 1


class TaStaleHost(TaHost):
    """A host with a temporary assign of its own, read after the nested component is drawn."""

    class Meta:
        template_name = "ta/stalehost.html"
        temporary_assigns = {"notes"}

    notes: list[str] = []

    async def note(self):
        self.notes = ["first", "second"]


class TaBlockHost(TaHost):
    """Draws a component with temporary assigns from ``{% component_block %}``, its fill reading a field of its own."""

    class Meta:
        template_name = "ta/blockhost.html"

    title: str = "T"


class TaChangingFillHost(TaHost):
    class Meta:
        template_name = "ta/changingfill.html"


class TaComponentFillHost(TaBlockHost):
    class Meta:
        template_name = "ta/componentfill.html"


class TaK(Component):
    class Meta:
        template_name = "ta/k.html"

    k: int = 0

    async def inc(self):
        self.k += 1

    async def same(self):
        pass


class TaJoinedK(TaK):
    async def joined(self):
        self.k = 5  # what it shows once connected


class TaLoadedK(Component):
    """Loads its own temporary assign in joined(), as the docs advise."""

    class Meta:
        template_name = "ta/loadedk.html"
        temporary_assigns = {"notes"}

    k: int = 0
    notes: list[str] = []

    async def joined(self):
        self.notes = ["x", "y"]


class TaHiddenK(Component):
    """joined() changes a signed field its template does not show."""

    class Meta:
        template_name = "ta/hiddenk.html"

    token: str = ""

    async def joined(self):
        self.token = "loaded"  # e.g. an id it looked up once connected


class TaG(Component):
    """Loads its temporary assign in joined() and passes its length to a grandchild that does not show it."""

    class Meta:
        template_name = "ta/g.html"
        temporary_assigns = {"notes"}

    notes: list[str] = []

    async def joined(self):
        self.notes = ["x", "y"]


class TaH(Component):
    class Meta:
        template_name = "ta/h.html"

    size: int = 0


class TaSlotBlockHost(TaHost):
    class Meta:
        template_name = "ta/slotblockhost.html"


class TaStaleFillHost(TaStaleHost):
    """Reads its own temporary assign in a fill."""

    class Meta:
        template_name = "ta/stalefill.html"


class TaStaleLiveFillHost(TaStaleHost):
    class Meta:
        template_name = "ta/stalelivefill.html"


class TaConstSlotHost(TaHost):
    class Meta:
        template_name = "ta/constslothost.html"


class TaDefaultSlotHost(TaStaleHost):
    class Meta:
        template_name = "ta/defaultslothost.html"


class TaKSlotHost(TaHost):
    class Meta:
        template_name = "ta/kslothost.html"


class TaConstDefaultSlotHost(TaHost):
    class Meta:
        template_name = "ta/constdefaultslothost.html"


class TaLiveFillHost(TaHost):
    class Meta:
        template_name = "ta/livefill.html"


class TaSlotHost(Component):
    """Puts a component with temporary assigns in another component's slot."""

    class Meta:
        template_name = "ta/slothost.html"

    child: str = "TaThis"


class TaFrame(Component):
    class Meta:
        template_name = "ta/frame.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass

    def last_diff(self) -> t.Any:
        return [payload for command, payload in self.commands if command == "render"][-1]["diff"]


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


async def page(name: str) -> tuple[WireviewConsumer, FakeOutbound, Component]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "c"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    component = await consumer.repo.join(name, {"id": "p"})
    await consumer.send_render(component)
    return consumer, outbound, component


async def event(consumer: WireviewConsumer, outbound: FakeOutbound, handler: str) -> str:
    await consumer.command_user_event("p", handler, {}, {})
    return json.dumps(outbound.last_diff(), ensure_ascii=False)


_DATA_STATE = re.compile(r' data-state="[^"]*"')


def html_now(component: Component) -> str:
    """What the client has, rebuilt from the server's last render, without the ``data-state`` tokens.

    A token is signed base64: "K0" not in a drawing failed whenever one happened to hold it.
    """
    return _DATA_STATE.sub("", component.wire._last_rendered.to_html())  # type: ignore[union-attr]


@pytest.mark.parametrize("name", ["TaThis", "TaName"])
async def test_an_unrelated_render_leaves_the_list_on_the_page(name):
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load")

    diff = await event(consumer, outbound, "bump")

    assert '"1"' in diff  # the control: the other field went out
    # The new data-state token is signed base64, which can hold "one" by chance
    sent = re.sub(r'"\.?[\w-]+:[\w-]+:[\w-]+"', '""', diff)
    assert "one" not in sent and '"d": []' not in sent, diff
    assert "<li>one</li><li>two</li>" in html_now(component)
    assert component.messages == []  # and the memory is still freed


@pytest.mark.parametrize("name", ["TaThis", "TaName"])
async def test_assigning_it_again_is_a_change(name):
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load")
    await event(consumer, outbound, "bump")

    diff = await event(consumer, outbound, "load_other")

    assert "three" in diff
    assert "<li>three</li>" in html_now(component) and "<li>one</li>" not in html_now(component)


async def test_assigning_the_default_on_purpose_empties_it():
    consumer, outbound, component = await page("TaThis")
    await event(consumer, outbound, "load")

    await event(consumer, outbound, "empty")

    assert "<li>" not in html_now(component)


async def test_changing_it_in_place_is_a_change():
    consumer, outbound, component = await page("TaThis")
    await event(consumer, outbound, "load")

    diff = await event(consumer, outbound, "append")

    assert "late" in diff
    assert "<li>late</li>" in html_now(component)


async def test_values_derived_from_it_stay_as_they_were():
    """A property, the property through ``this``, and a filter on the name."""
    consumer, outbound, component = await page("TaDerived")
    await event(consumer, outbound, "load")

    diff = await event(consumer, outbound, "bump")

    assert '"1"' in diff
    assert "<i>2</i><s>2</s><u>2</u>" in html_now(component), diff


async def test_inside_a_block_only_its_own_part_stays():
    consumer, outbound, component = await page("TaBlock")
    await event(consumer, outbound, "load")

    diff = await event(consumer, outbound, "bump")

    assert '"1"' in diff, "the count in the same block went out"
    assert "<li>one</li><li>two</li>" in html_now(component)
    assert "has messages" in html_now(component), "a condition on it keeps its branch"


async def test_a_part_that_reads_other_names_too_renders_from_what_it_has():
    """The loop over ``rows`` reads ``messages`` in each item. It cannot keep its old
    value -- ``rows`` may be what changed -- so it renders with the reset list."""
    consumer, outbound, component = await page("TaNested")
    await event(consumer, outbound, "load")
    assert "<li>a:2</li><li>b:2</li>" in html_now(component)

    diff = await event(consumer, outbound, "add_row")

    assert "<li>a</li><li>b</li><li>c</li>" in html_now(component), diff


async def test_a_block_whose_content_changed_is_not_kept_for_its_condition():
    """The condition reads only the stale list, the content reads ``count``. Kept
    whole, the block would show the old count forever. It is evaluated again, with
    the reset list -- which is what Phoenix does when a block's other assign changes."""
    consumer, outbound, component = await page("TaGuarded")
    await event(consumer, outbound, "load")
    assert "<b>0</b>" in html_now(component)

    await event(consumer, outbound, "bump")

    assert "<b>0</b>" not in html_now(component)
    assert "<i>1</i>" in html_now(component)


async def test_the_signed_state_leaves_it_out():
    """It is signed while the render holds it: ten thousand rows went into a page attribute."""
    _, _, component = await page("TaThis")
    component.messages = ["one", "two"]

    state = unsign_state(sign_state(component), type(component)._fqn)

    assert "messages" not in state
    assert state["count"] == 0


# --- a component drawn in another component's pass ------------------------------------------------
#
# A nested ``{% component %}`` renders on its own for its events, and in its host's pass
# whenever the host renders -- the host's diff then puts the host's drawing of it on the
# page. That drawing must keep what the component's own render showed of a reset
# temporary assign, as the component's own next render would.


async def nested_page(host: str, child: str) -> tuple[WireviewConsumer, FakeOutbound, Component, Component]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "c"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    host_component = await consumer.repo.join(host, {"id": "p", "child": child})
    await consumer.send_render(host_component)
    for nested in ("f", "g", "k"):
        if (instance := consumer.repo.get(nested)) is not None:
            await consumer.command_join(type(instance)._fqn, sign_state(instance))
    return consumer, outbound, host_component, consumer.repo.get("g")


@pytest.mark.parametrize("child", ["TaThis", "TaName", "TaDerived", "TaBlock", "TaLoops"])
async def test_the_hosts_render_leaves_a_nested_components_list_on_the_page(child):
    consumer, outbound, host, nested = await nested_page("TaHost", child)
    await consumer.command_user_event("g", "load", {}, {})
    assert "one" in html_now(nested)

    await consumer.command_user_event("p", "bump", {}, {})

    drawn = html_now(host)
    assert "<b>1</b>" in drawn, "the control: what the host passed went out"
    assert ("<li>one</li><li>two</li>" if child != "TaDerived" else "<i>2</i><s>2</s><u>2</u>") in drawn, drawn
    assert nested.messages == []  # type: ignore[attr-defined]


async def test_the_nested_components_next_render_after_the_hosts_still_has_it():
    consumer, outbound, host, nested = await nested_page("TaHost", "TaThis")
    await consumer.command_user_event("g", "load", {}, {})
    await consumer.command_user_event("p", "bump", {}, {})

    await consumer.command_user_event("g", "bump", {}, {})

    assert "<li>one</li><li>two</li>" in html_now(nested)
    await consumer.command_user_event("p", "bump", {}, {})
    assert "<li>one</li><li>two</li>" in html_now(host)


async def test_assigning_it_again_reaches_the_hosts_render():
    consumer, outbound, host, nested = await nested_page("TaHost", "TaThis")
    await consumer.command_user_event("g", "load", {}, {})
    await consumer.command_user_event("p", "bump", {}, {})

    await consumer.command_user_event("g", "load_other", {}, {})
    await consumer.command_user_event("p", "bump", {}, {})

    assert "<li>three</li>" in html_now(host) and "<li>one</li>" not in html_now(host)


async def test_a_list_the_host_drew_before_the_component_rendered_on_its_own_is_not_kept():
    """Nothing to keep before the component's own render: the host draws what it has."""
    consumer, outbound, host, nested = await nested_page("TaHost", "TaThis")

    await consumer.command_user_event("p", "bump", {}, {})

    assert "<li>" not in html_now(host)
    assert "<b>1</b>" in html_now(host)


async def test_a_part_in_a_branch_the_component_did_not_draw_last_renders_from_what_it_has():
    """Nothing lines up with its last render there, so the host draws what its own next render would."""
    consumer, outbound, host, nested = await nested_page("TaHost", "TaBranch")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<q>2</q>" in html_now(nested)

    await consumer.command_user_event("p", "bump", {}, {})

    assert "<p>0</p>" in html_now(host)
    await consumer.command_user_event("g", "bump", {}, {})
    assert "<p>0</p>" in html_now(nested)


@pytest.mark.parametrize("child", ["TaThis", "TaName"])
async def test_the_host_keeps_its_own_list_drawn_after_a_nested_components(child):
    """The nested component tracks its reads inside the host's render; the host's tracking resumes after it."""
    consumer, outbound, host, nested = await nested_page("TaStaleHost", child)
    await consumer.command_user_event("p", "note", {}, {})
    await consumer.command_user_event("g", "load", {}, {})

    await consumer.command_user_event("p", "bump", {}, {})

    drawn = html_now(host)
    assert "<i>1</i>" in drawn
    assert "<li>one</li><li>two</li>" in drawn and "<li>first</li><li>second</li>" in drawn, drawn


async def test_a_slots_owner_leaves_a_nested_components_list_on_the_page():
    consumer, outbound, host, nested = await nested_page("TaSlotHost", "TaThis")
    await consumer.command_user_event("g", "load", {}, {})

    await consumer.command_user_event("f", "click", {}, {})

    drawn = html_now(consumer.repo.get("f"))
    assert "<s>1</s>" in drawn, "the control: the owner's own change went out"
    assert "<li>one</li><li>two</li>" in drawn, drawn


async def test_the_hosts_render_leaves_the_list_of_a_component_block_whose_fill_reads_the_host():
    """The fill holds the host's markers in the host's pass and plain text in the component's own render."""
    consumer, outbound, host, nested = await nested_page("TaBlockHost", "TaSlotted")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<li>one</li><li>two</li>" in html_now(nested)

    await consumer.command_user_event("p", "bump", {}, {})

    drawn = html_now(host)
    assert "<i>1</i>" in drawn and "<em>T</em>" in drawn, "the control: the host's change went out"
    assert "<li>one</li><li>two</li>" in drawn, drawn


async def test_the_hosts_render_leaves_the_list_of_a_component_block_whose_fill_holds_a_component():
    """The component's own render draws the fill's component with numbered parts, so the host's pass keeps them."""
    consumer, outbound, host, nested = await nested_page("TaComponentFillHost", "TaSlotted")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<li>one</li><li>two</li>" in html_now(nested)

    await consumer.command_user_event("p", "bump", {}, {})

    drawn = html_now(host)
    assert "<i>1</i>" in drawn and "<em>T</em>" in drawn, "the control: the host's change went out"
    assert "K0</p>" in drawn, "the fill's component is drawn"
    assert "<li>one</li><li>two</li>" in drawn, drawn


async def test_a_fill_whose_text_changed_draws_the_list_as_the_components_next_render_would():
    """Its own next render has the new fill as text, so nothing there lines up with its last render either."""
    consumer, outbound, host, nested = await nested_page("TaChangingFillHost", "TaSlotted")
    await consumer.command_user_event("g", "load", {}, {})

    await consumer.command_user_event("p", "bump", {}, {})

    assert "<em>1</em><ul></ul>" in html_now(host)
    await consumer.command_user_event("g", "bump", {}, {})
    assert "<em>1</em><ul></ul>" in html_now(nested)


async def test_a_fill_naming_a_live_component_keeps_the_list_and_the_reference():
    consumer, outbound, host, nested = await nested_page("TaLiveFillHost", "TaSlotted")
    await consumer.command_user_event("g", "load", {}, {})
    outbound.commands.clear()

    await consumer.command_user_event("p", "bump", {}, {})

    drawn = html_now(host)
    assert "<li>one</li><li>two</li>" in drawn, drawn
    renders = [payload for command, payload in outbound.commands if command == "render"]
    assert "<!--@wv:lc-->" not in json.dumps(renders), "the LiveComponent went out as text"
    assert consumer.repo.get("lc") is not None


async def test_a_kept_part_naming_a_live_component_keeps_it_named_in_the_hosts_render():
    """Put back as text, the reference reached the page as a comment and the LiveComponent's element left it.

    Drawn from what it had instead, the host's render took the part off the page
    while the component's own renders kept it: its next one did not bring the
    LiveComponent back, and the server held one the page no longer showed.
    """
    consumer, outbound, host, nested = await nested_page("TaHost", "TaWithLive")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<li>one</li>" in html_now(nested)
    outbound.commands.clear()

    await consumer.command_user_event("p", "bump", {}, {})

    renders = [payload for command, payload in outbound.commands if command == "render"]
    assert "<b>1</b>" in html_now(host), "the control: what the host passed went out"
    assert "<!--@wv:lc-->" not in json.dumps(renders), "the LiveComponent went out as text"
    assert "<li>one</li><li>two</li></ul><!--@wv:lc-->" in html_now(host), html_now(host)
    assert "lc" in renders[-1]["children"], "the page has nothing to draw the LiveComponent from"


async def test_a_kept_loop_of_live_components_keeps_them_named_in_the_hosts_render():
    """Each item of the loop goes back as the item, its references inside it, not as text."""
    consumer, outbound, host, nested = await nested_page("TaHost", "TaLiveList")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<!--@wv:one-->" in html_now(nested), html_now(nested)
    outbound.commands.clear()

    await consumer.command_user_event("p", "bump", {}, {})

    renders = [payload for command, payload in outbound.commands if command == "render"]
    assert "<b>1</b>" in html_now(host), "the control: what the host passed went out"
    assert "<!--@wv:one-->" not in json.dumps(renders), "a LiveComponent went out as text"
    assert "<!--@wv:one--><!--@wv:two-->" in html_now(host), html_now(host)
    assert consumer.repo.get("one") is not None and consumer.repo.get("two") is not None


async def test_the_live_component_the_hosts_render_kept_is_on_the_page_whenever_on_the_server():
    """Whether the server holds the LiveComponent is whether the page shows it, render after render."""
    consumer, outbound, host, nested = await nested_page("TaHost", "TaWithLive")

    async def agree(target: str, handler: str) -> None:
        # The page shows the component as the render that last reached it drew it
        await consumer.command_user_event(target, handler, {}, {})
        drawn = html_now(host if target == "p" else nested)
        assert ("<!--@wv:lc-->" in drawn) == (consumer.repo.get("lc") is not None), drawn

    await agree("g", "load")
    await agree("p", "bump")
    await agree("g", "bump")
    await agree("g", "load")
    await agree("p", "bump")
    assert consumer.repo.get("lc") is not None
    await agree("g", "empty")
    assert consumer.repo.get("lc") is None, "the control: the list it went with is gone"


# --- what a kept part holds that is not the component's own names (R10) ---------------------------
#
# A part kept for a reset temporary assign is what the component's last render drew. A
# LiveComponent in it is only a reference, so keeping it keeps the LiveComponent. A nested
# component, a slot's fill or an included template draws something the component's names
# do not decide; kept, it showed that as it was.


async def join(consumer: WireviewConsumer, id_: str) -> Component:
    instance = consumer.repo.get(id_)
    assert instance is not None, id_
    await consumer.command_join(type(instance)._fqn, sign_state(instance))
    return instance


@pytest.mark.parametrize("name", ["TaWithLive", "TaLiveList"])
async def test_a_live_component_in_a_kept_part_stays_on_the_server(name):
    """The page keeps the LiveComponent with the part; the server kept it only while the template named it."""
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load")
    ids = ["lc"] if name == "TaWithLive" else ["one", "two"]
    assert all(consumer.repo.get(id_) is not None for id_ in ids)

    await event(consumer, outbound, "bump")

    drawn = html_now(component)
    assert "<b>1</b>" in drawn, "the control: the other field went out"
    assert all(f"<!--@wv:{id_}-->" in drawn for id_ in ids), drawn
    assert all(consumer.repo.get(id_) is not None for id_ in ids), "the page shows a LiveComponent the server let go"


@pytest.mark.parametrize("name", ["TaWithK", "TaWithKBlock", "TaKList"])
async def test_a_kept_part_does_not_put_back_a_nested_components_old_drawing(name):
    """Put back, the nested component's drawing and data-state went back to before its own change."""
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load_other" if name == "TaKList" else "load")  # one item: one id
    k = await join(consumer, "k")
    await consumer.command_user_event("k", "inc", {}, {})
    assert "K1" in html_now(k)

    await event(consumer, outbound, "bump")

    drawn = html_now(component)
    assert "<b>1</b>" in drawn, "the control: the other field went out"
    assert "K0" not in drawn, drawn


@pytest.mark.parametrize(
    ("name", "handler", "old"), [("TaWithNotes", "note", "P<i>0</i>"), ("TaWithLabel", "relabel", "E<i>a</i>")]
)
async def test_a_kept_part_does_not_put_back_a_drawing_that_moved_outside_the_signed_state(name, handler, old):
    """A temporary assign or an excluded field moved the nested component's drawing, not its data-state (I6)."""
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load")
    k = await join(consumer, "k")
    await consumer.command_user_event("k", handler, {}, {})
    assert old not in html_now(k)

    await event(consumer, outbound, "bump")

    drawn = html_now(component)
    assert "<b>1</b>" in drawn, "the control: the other field went out"
    assert old not in drawn, drawn


async def test_a_part_inside_one_kept_last_time_keeps_its_record():
    """The loop did not run while the block around it was kept, and still drew K back then (I6)."""
    consumer, outbound, component = await page("TaTwo")
    await event(consumer, outbound, "load")
    k = await join(consumer, "k")
    await event(consumer, outbound, "bump")  # both stale: the block kept, the loop never ran
    assert "K0" in html_now(component)
    await consumer.command_user_event("k", "inc", {}, {})
    assert "K1" in html_now(k)

    await event(consumer, outbound, "note")  # notes new, messages stale: the loop runs and is kept

    drawn = html_now(component)
    assert "<s>2</s>" in drawn, "the control: the block drew its new notes"
    assert "K0" not in drawn, drawn


async def test_a_render_of_its_own_that_changed_nothing_does_not_move_a_nested_component():
    consumer, outbound, component = await page("TaWithK")
    await event(consumer, outbound, "load")
    await join(consumer, "k")
    await consumer.command_user_event("k", "same", {}, {})

    await event(consumer, outbound, "bump")

    assert "<li>one</li><li>two</li>" in html_now(component), html_now(component)


async def test_the_join_does_not_move_a_nested_component():
    """Its join's answer draws what the pass drew; counted, every kept part holding it drew again."""
    consumer, outbound, component = await page("TaWithK")
    await event(consumer, outbound, "load")
    await join(consumer, "k")

    await event(consumer, outbound, "bump")

    assert "<li>one</li><li>two</li>" in html_now(component), html_now(component)


def signed_states(consumer: WireviewConsumer, component: Component) -> dict[str, dict[str, t.Any]]:
    """The signed state of each component ``component``'s last render draws, by id: what a reconnect joins with."""
    drawn = component.wire._last_rendered.to_html()  # type: ignore[union-attr]
    found = re.findall(r'id="([^"]+)" data-name="[^"]+" data-state="([^"]+)"', drawn)
    return {id_: unsign_state(token, type(consumer.repo.get(id_))._fqn) for id_, token in found}


def assert_signed_as_on_the_server(consumer: WireviewConsumer, component: Component) -> None:
    for id_, state in signed_states(consumer, component).items():
        assert state == state_of(consumer.repo.get(id_)), f"the host put back {id_}'s signed state: {state}"


@pytest.mark.parametrize(
    ("name", "joined"), [("TaWithJoinedK", "K5"), ("TaWithLoadedK", "<s>2</s>"), ("TaWithHiddenK", None)]
)
async def test_a_join_that_drew_something_new_moves_a_nested_component(name, joined):
    """joined() changed what the pass drew: a kept part holding the pass's drawing is drawn again.

    A signed field the page does not show counts too: a reconnect joins with
    the ``data-state`` the host drew.
    """
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load")
    k = await join(consumer, "k")
    if joined is not None:
        assert joined in html_now(k), "the control: the join's answer drew what joined() did"
    else:
        assert signed_states(consumer, k)["k"]["token"] == "loaded", "the control: the join's answer signed it"

    await event(consumer, outbound, "bump")

    drawn = html_now(component)
    assert "<b>1</b>" in drawn, "the control: the other field went out"
    assert "K0" not in drawn and "<s>0</s>" not in drawn, "the host put k back as the pass drew it"
    assert_signed_as_on_the_server(consumer, component)


@pytest.mark.parametrize("grandchild_joins", [True, False])
async def test_a_join_that_changed_a_grandchild_moves_the_nested_component(grandchild_joins):
    """g's joined() changed only what g passes h, which h does not show: h's ``data-state`` is still new."""
    consumer, outbound, component = await page("TaWithG")
    await event(consumer, outbound, "load")
    await join(consumer, "g")
    assert consumer.repo.get("h").size == 2, "the control: g's joined() passed h its notes"
    if grandchild_joins:
        await join(consumer, "h")

    await event(consumer, outbound, "bump")

    assert "<b>1</b>" in html_now(component), "the control: the other field went out"
    assert_signed_as_on_the_server(consumer, component)


async def test_a_drawing_is_the_same_whenever_its_state_was_signed():
    """A token signed later for the same state draws the same; another state does not."""
    _, _, component = await page("TaThis")
    with mock.patch("django.core.signing.time.time", return_value=1_000_000):
        component.wire._state_token = None
        first = sign_state(component)
    with mock.patch("django.core.signing.time.time", return_value=2_000_000):
        component.wire._state_token = None
        later = sign_state(component)
        component.count = 1
        other = sign_state(component)
    assert first != later, "the control: signed at another time"

    def drawing(token: str) -> str:
        return page_drawing(f'<div data-state="{token}">x</div>')

    assert drawing(first) == drawing(later)
    assert drawing(first) != drawing(other)


@pytest.mark.parametrize("name", ["TaRows", "TaRowsNoId"])
async def test_a_kept_part_keeps_what_nested_components_drew_while_they_did_not_move(name):
    """Rows that never change on their own are drawn as they were: the list stays, as it did before 1.0."""
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load")
    for row in ("one", "two") if name == "TaRows" else ():
        await join(consumer, row)  # the page joins what it is handed

    diff = await event(consumer, outbound, "bump")

    drawn = html_now(component)
    assert "<b>1</b>" in drawn, "the control: the other field went out"
    assert ">one</li>" in drawn and ">two</li>" in drawn, diff


async def test_a_kept_part_draws_again_once_a_nested_component_in_it_moved():
    consumer, outbound, component = await page("TaRows")
    await event(consumer, outbound, "load")
    one = await join(consumer, "one")
    await event(consumer, outbound, "bump")
    assert ">one</li>" in html_now(component)
    await consumer.command_user_event("one", "shout", {}, {})
    assert ">ONE</li>" in html_now(one)

    await event(consumer, outbound, "bump")

    assert ">one</li>" not in html_now(component), "the row went back to before its own change"


async def test_a_kept_part_keeps_a_nested_component_the_connection_does_not_hold():
    """Drawn without the page's repository and not joined yet, it has not rendered on its own: nothing moved."""
    consumer, outbound, component = await page("TaRowsFunc")
    await event(consumer, outbound, "load")
    assert consumer.repo.get("k") is None, "the control: the connection holds no instance under the id"

    await event(consumer, outbound, "bump")

    drawn = html_now(component)
    assert "<b>1</b>" in drawn, "the control: the other field went out"
    assert "<li>one</li><li>two</li>" in drawn and "K0" in drawn, drawn


async def test_a_component_a_function_component_draws_in_a_kept_part_is_seen():
    """The template of ``{% func %}`` is not the component's, so only what the render drew tells."""
    consumer, outbound, component = await page("TaRowsFunc")
    await event(consumer, outbound, "load")
    # Drawn without the page's repository, the page joins it from what it was handed
    state = re.search(r'id="k" data-name="[^"]+" data-state="([^"]+)"', component.wire._last_rendered.to_html())  # type: ignore[union-attr]
    assert state is not None, html_now(component)
    await consumer.command_join(TaK._fqn, state.group(1))
    k = consumer.repo.get("k")
    await event(consumer, outbound, "bump")
    assert "<li>one</li>" in html_now(component), "the control: nothing moved, so the list stays"
    await consumer.command_user_event("k", "inc", {}, {})
    assert "K1" in html_now(k)

    await event(consumer, outbound, "bump")

    assert "K0" not in html_now(component), html_now(component)


async def test_a_block_whose_fill_did_not_change_keeps_the_list():
    consumer, outbound, host, nested = await nested_page("TaConstSlotHost", "TaSlotBlock")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<li>one</li><li>two</li></ul><em>static</em>" in html_now(nested)

    await consumer.command_user_event("p", "bump", {}, {})

    assert "<i>1</i>" in html_now(host), "the control: the host's change went out"
    assert "<li>one</li><li>two</li></ul><em>static</em>" in html_now(host), html_now(host)
    await consumer.command_user_event("g", "bump", {}, {})
    assert "<li>one</li><li>two</li></ul><em>static</em>" in html_now(nested), html_now(nested)


async def test_a_block_whose_default_slot_did_not_change_keeps_the_list():
    """Content outside any fill is the default slot, compared like a named one."""
    consumer, outbound, host, nested = await nested_page("TaConstDefaultSlotHost", "TaDefaultSlotBlock")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<li>one</li><li>two</li></ul><em>static</em>" in html_now(nested), html_now(nested)

    await consumer.command_user_event("g", "bump", {}, {})

    assert "<b>1</b>" in html_now(nested), "the control: the other field went out"
    assert "<li>one</li><li>two</li></ul><em>static</em>" in html_now(nested), html_now(nested)


async def test_a_kept_block_does_not_put_back_a_component_a_slot_drew_in_it():
    """The fill draws the component; the block records it as it records one of its own."""
    consumer, outbound, host, nested = await nested_page("TaKSlotHost", "TaSlotBlock")
    await consumer.command_user_event("g", "load", {}, {})
    assert "K0" in html_now(nested), html_now(nested)
    k = consumer.repo.get("k")  # the host's pass drew the fill, so the page joined it already
    assert k is not None and k.wire.has_joined
    await consumer.command_user_event("k", "inc", {}, {})
    assert "K1" in html_now(k)

    await consumer.command_user_event("g", "bump", {}, {})

    assert "K0" not in html_now(nested), html_now(nested)


async def test_a_default_slot_reading_the_hosts_temporary_assign_is_drawn_alike_by_both():
    """Content outside any fill is the default slot; the host keeps what it drew last there too."""
    consumer, outbound, host, nested = await nested_page("TaDefaultSlotHost", "TaDefaultSlotted")
    await consumer.command_user_event("p", "note", {}, {})
    assert "<em>2</em>" in html_now(host)

    await consumer.command_user_event("p", "bump", {}, {})

    assert "<i>1</i><div" in html_now(host) and "<em>2</em>" in html_now(host), html_now(host)
    await consumer.command_user_event("g", "bump", {}, {})
    assert "<b>1</b><em>2</em>" in html_now(nested), html_now(nested)


async def test_a_block_holding_a_template_named_at_render_time_is_drawn_again():
    """What ``{% include tpl %}`` reads is not known until it renders, and the branch did not."""
    consumer, outbound, component = await page("TaNamedInclude")
    await event(consumer, outbound, "load")
    assert "<s>0</s>" in html_now(component)

    await event(consumer, outbound, "bump")

    assert "<b>1</b>" in html_now(component), "the control: the other field went out"
    assert "<s>0</s>" not in html_now(component), html_now(component)


async def test_a_block_holding_a_template_it_cannot_find_is_drawn_again():
    """The branch never renders, so nothing fails; what it would read cannot be known."""
    consumer, outbound, component = await page("TaMissingInclude")
    await event(consumer, outbound, "load")
    assert "<li>one</li>" in html_now(component)

    await event(consumer, outbound, "bump")

    assert "<b>1</b>" in html_now(component), "the control: the other field went out"
    assert "<li>one</li>" not in html_now(component), html_now(component)


async def test_the_hosts_render_does_not_put_back_a_nested_components_old_drawing():
    consumer, outbound, host, nested = await nested_page("TaHost", "TaWithK")
    await consumer.command_user_event("g", "load", {}, {})
    k = await join(consumer, "k")
    await consumer.command_user_event("k", "inc", {}, {})
    assert "K1" in html_now(k)

    await consumer.command_user_event("p", "bump", {}, {})

    assert "K0" not in html_now(host), html_now(host)


async def test_a_block_that_draws_a_slot_shows_the_fills_change():
    """The fill is the host's value, which the block's reads never see."""
    consumer, outbound, host, nested = await nested_page("TaSlotBlockHost", "TaSlotBlock")
    await consumer.command_user_event("g", "load", {}, {})
    assert "<em>0</em>" in html_now(nested)

    await consumer.command_user_event("p", "bump", {}, {})

    drawn = html_now(host)
    assert "<i>1</i>" in drawn, "the control: the host's change went out"
    assert "<em>0</em>" not in drawn, drawn
    await consumer.command_user_event("g", "bump", {}, {})
    assert "<em>0</em>" not in html_now(nested), html_now(nested)


async def test_an_included_template_that_reads_only_it_stays():
    consumer, outbound, component = await page("TaInclude")
    await event(consumer, outbound, "load")

    diff = await event(consumer, outbound, "bump")

    assert '"s"' not in diff, f"a full render: {diff}"
    assert "<li>one</li><li>two</li>" in html_now(component), diff
    assert "<b>1</b>" in html_now(component)


async def test_an_included_template_that_reads_another_name_in_a_branch_it_did_not_draw_is_drawn_again():
    """As for ``{% if %}``: the branch that would read ``count`` did not render."""
    consumer, outbound, component = await page("TaIncludeOther")
    await event(consumer, outbound, "load")
    assert "<i>0</i>" in html_now(component)

    await event(consumer, outbound, "bump")

    assert "<i>0</i>" not in html_now(component), html_now(component)
    assert "<b>1</b>" in html_now(component)


async def test_a_block_holding_an_included_template_looks_into_it():
    consumer, outbound, component = await page("TaBlockInclude")
    await event(consumer, outbound, "load")
    assert "<s>0</s>" in html_now(component)

    await event(consumer, outbound, "bump")

    drawn = html_now(component)
    assert "<li>one</li><li>two</li>" in drawn, "the included template reads only the list"
    assert "<s>0</s>" not in drawn, "the other one reads count"


async def test_a_fill_reading_the_hosts_temporary_assign_is_drawn_alike_by_both():
    """The host keeps what its fill drew last; the component's own render draws the fill it was handed."""
    consumer, outbound, host, nested = await nested_page("TaStaleFillHost", "TaSlotted")
    await consumer.command_user_event("p", "note", {}, {})
    await consumer.command_user_event("g", "load", {}, {})
    assert "<em>2</em>" in html_now(nested)

    await consumer.command_user_event("p", "bump", {}, {})

    drawn = html_now(host)
    assert "<i>1</i>" in drawn, "the control: the host's change went out"
    assert "<em>2</em><ul><li>one</li><li>two</li></ul>" in drawn, drawn
    await consumer.command_user_event("g", "bump", {}, {})
    assert "<em>2</em><ul><li>one</li><li>two</li></ul>" in html_now(nested), html_now(nested)


async def test_a_live_components_fill_reading_the_hosts_temporary_assign_stays():
    consumer, outbound, host = await page("TaStaleLiveFillHost")
    await consumer.command_user_event("p", "note", {}, {})
    live = consumer.repo.get("lc")
    assert "<em>2</em>" in html_now(live)

    await consumer.command_user_event("p", "bump", {}, {})

    assert "<i>1</i>" in html_now(host), "the control: the host's change went out"
    assert "<em>2</em>" in html_now(live), html_now(live)
