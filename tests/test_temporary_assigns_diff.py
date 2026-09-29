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
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.state import sign_state, unsign_state
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


def html_now(component: Component) -> str:
    """What the client has, rebuilt from the server's last render."""
    return component.wire._last_rendered.to_html()  # type: ignore[union-attr]


@pytest.mark.parametrize("name", ["TaThis", "TaName"])
async def test_an_unrelated_render_leaves_the_list_on_the_page(name):
    consumer, outbound, component = await page(name)
    await event(consumer, outbound, "load")

    diff = await event(consumer, outbound, "bump")

    assert '"1"' in diff  # the control: the other field went out
    assert "one" not in diff and '"d": []' not in diff, diff
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
