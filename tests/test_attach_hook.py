"""``attach_hook()`` hooks run where the server does the work (#110).

The hooks were registered and documented -- the lifecycle guide uses them for
rate limiting and an audit log -- but nothing ran them: ``_run_hooks`` had no
caller, the same shape as #75. So these tests go through the consumer, the way
a browser's event, navigation and render reach a component, and attach the
hooks the way the guide does, from an ``on_mount`` hook.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.repository import ComponentRepository
from wireview.testing import mount

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {"ah/counter.html": "{% load wireview %}<div {% tag_header %}>{{ this.count }}</div>"}

#: What happened, in order.
CALLS: list[tuple[t.Any, ...]] = []


class Hooks:
    """Attaches one hook per stage; ``halt`` in the component's state makes them refuse."""

    @staticmethod
    async def on_mount(component, params, session):
        async def on_event(event, params):
            CALLS.append(("hook:event", event, dict(params)))
            return {"halt": True} if component.halt else {"cont": True}

        async def on_params(params, uri):
            CALLS.append(("hook:params", uri))
            return {"halt": True} if component.halt else {"cont": True}

        async def after_render():
            CALLS.append(("hook:render", component.count))

        component.attach_hook("event", "handle_event", on_event)
        component.attach_hook("params", "handle_params", on_params)
        component.attach_hook("render", "after_render", after_render)
        return {"cont": True}


class AhCounter(Component):
    class Meta:
        template_name = "ah/counter.html"
        on_mount = [Hooks]

    count: int = 0
    halt: bool = False

    async def increment(self, by: int = 1):
        CALLS.append(("increment", by))
        self.count += by

    async def params_changed(self, params, uri):
        CALLS.append(("params_changed", uri))


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


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


async def joined(**state) -> tuple[WireviewConsumer, Component]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    consumer.outbound = FakeOutbound()  # type: ignore[assignment]
    component = await consumer.repo.join("AhCounter", {"id": "c", **state})
    await consumer.send_render(component)
    CALLS.clear()
    return consumer, component


def kinds() -> list[str]:
    return [call[0] for call in CALLS]


async def test_a_handle_event_hook_sees_the_event_before_the_handler():
    consumer, component = await joined()

    await consumer.command_user_event("c", "increment", {}, {"by": 2})

    assert CALLS[0] == ("hook:event", "increment", {"by": 2})
    assert ("increment", 2) in CALLS
    assert component.count == 2


async def test_a_halting_handle_event_hook_stops_the_handler():
    # The guide's rate limiter: a hook that halts is the whole point of the stage.
    consumer, component = await joined(halt=True)

    await consumer.command_user_event("c", "increment", {}, {"by": 2})

    assert "increment" not in kinds()
    assert component.count == 0


async def test_a_handle_params_hook_runs_before_params_changed():
    consumer, _ = await joined()

    await consumer.command_params_changed({"page": "2"}, "?page=2")

    assert kinds()[:2] == ["hook:params", "params_changed"]


async def test_a_halting_handle_params_hook_skips_params_changed():
    consumer, _ = await joined(halt=True)

    await consumer.command_params_changed({"page": "2"}, "?page=2")

    assert "hook:params" in kinds()
    assert "params_changed" not in kinds()


async def test_an_after_render_hook_runs_after_each_render():
    consumer, _ = await joined()

    await consumer.command_user_event("c", "increment", {}, {"by": 3})

    assert CALLS[-1] == ("hook:render", 3)


async def test_a_detached_hook_no_longer_runs():
    consumer, component = await joined()

    assert component.detach_hook("event") is True
    await consumer.command_user_event("c", "increment", {}, {})

    assert "hook:event" not in kinds()
    assert component.count == 1


async def test_hooks_belong_to_the_instance_they_were_attached_to():
    _, first = await joined()
    _, second = await joined()

    first.detach_hook("event")

    assert [hook["name"] for hook in second._lifecycle_hooks["handle_event"]] == ["event"]


async def test_the_test_helper_does_not_get_past_a_halting_hook():
    view = await mount(AhCounter, halt=True)

    await view.call("increment", by=5)

    assert view.component.count == 0
