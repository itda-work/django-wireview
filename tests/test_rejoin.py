"""A second join for an id on the same connection (#81).

The client joins only elements whose ``data-is-live`` is false, so a second join for an
id this connection already holds means new DOM arrived for it. ``joined()`` stays once per
instance: the instance that already joined leaves and a fresh one joins. An instance a
parent's template pass created but that never joined is adopted by its own join.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "rj/page.html": "{% load wireview %}<main {% tag_header %}>{% component 'RjChild' id='child' n=this.n %}</main>",
    "rj/child.html": "{% load wireview %}<p {% tag_header %}>{{ this.n }}</p>",
    "rj/parent.html": "{% load wireview %}<main {% tag_header %}>{% live_component 'RjLive' id='lc' %}</main>",
    "rj/live.html": "{% load wireview %}<p {% live_tag_header %}>{{ this.id }}</p>",
}

CALLS: list[tuple[str, str, int]] = []


class RjPage(Component):
    _template_name = "rj/page.html"
    n: int = 0

    async def joined(self):
        CALLS.append(("joined", self.id, id(self)))

    async def leaving(self):
        CALLS.append(("leaving", self.id, id(self)))


class RjChild(Component):
    _template_name = "rj/child.html"
    n: int = 0

    async def joined(self):
        CALLS.append(("joined", self.id, id(self)))

    async def leaving(self):
        CALLS.append(("leaving", self.id, id(self)))


class RjParent(Component):
    _template_name = "rj/parent.html"


class RjLive(LiveComponent):
    _template_name = "rj/live.html"

    async def leaving(self):
        CALLS.append(("leaving", self.id, id(self)))


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...


@pytest.fixture(autouse=True)
def _templates_and_calls():
    CALLS.clear()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield
    CALLS.clear()


def make_consumer() -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def signed(component_class, **state) -> str:
    from wireview.core.meta import WireviewMeta

    return sign_state(component_class(user=AnonymousUser(), wire=WireviewMeta(params={}), **state))


def kinds(name: str) -> list[str]:
    return [kind for kind, cid, _ in CALLS if cid == name]


async def test_a_second_join_for_a_joined_id_replaces_the_instance():
    consumer, _ = make_consumer()
    await consumer.command_join("RjChild", signed(RjChild, id="solo", n=1))
    first = consumer.repo.get("solo")

    await consumer.command_join("RjChild", signed(RjChild, id="solo", n=2))
    second = consumer.repo.get("solo")

    assert second is not first
    assert [(k, i) for k, c, i in CALLS if c == "solo"] == [
        ("joined", id(first)),
        ("leaving", id(first)),
        ("joined", id(second)),
    ]
    assert second.n == 2


async def test_the_replacement_renders_in_full_for_the_new_dom():
    consumer, outbound = make_consumer()
    await consumer.command_join("RjChild", signed(RjChild, id="solo", n=1))
    outbound.commands.clear()

    await consumer.command_join("RjChild", signed(RjChild, id="solo", n=1))

    renders = [p for c, p in outbound.commands if c == "render"]
    assert len(renders) == 1 and "s" in renders[0]["diff"], "a fresh instance sends statics, not a partial"


async def test_a_nested_component_created_by_the_parents_render_is_adopted_by_its_own_join():
    consumer, _ = make_consumer()
    await consumer.command_join("RjPage", signed(RjPage, id="page", n=3))
    created = consumer.repo.get("child")
    assert created is not None and kinds("child") == [], "the parent's template pass created it, no joined() yet"

    await consumer.command_join("RjChild", signed(RjChild, id="child", n=3))

    assert consumer.repo.get("child") is created, "the same instance completes its join"
    assert kinds("child") == ["joined"], "no leaving() for an instance that never joined"


async def test_rejoining_a_parent_retires_its_live_components_too():
    consumer, _ = make_consumer()
    await consumer.command_join("RjParent", signed(RjParent, id="par"))
    first_child = consumer.repo.get("lc")
    assert first_child is not None

    await consumer.command_join("RjParent", signed(RjParent, id="par"))

    assert ("leaving", "lc", id(first_child)) in CALLS
    assert consumer.repo.get("lc") is not first_child, "the fresh parent rendered a fresh child"
