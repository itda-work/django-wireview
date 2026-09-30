"""A component's async tasks end when the component does (#95).

``start_async`` and ``assign_async`` run outside the event that started them.
Nothing cancelled them when the component left -- a closed tab, a ``leave``,
an instance replaced by a new join or discarded after raising -- so they ran
to the end, held the instance in memory, and then asked a session that no
longer had the component for a render.

Two more defects in the same place:

- a task replaced under the same name removed its *replacement* from the
  bookkeeping when it finished being cancelled, so the replacement could no
  longer be cancelled by name or by leaving;
- ``assign_async`` kept no reference to its task, which asyncio may then
  garbage collect while it runs.
"""

import asyncio
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from testproj.waiting import eventually

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.repository import ComponentRepository
from wireview.testing import mount

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

HANDLED: list[tuple[str, str]] = []


class Slow(Component):
    class Meta:
        template_name = "todo/counter.html"

    async def forever(self) -> None:
        await asyncio.Event().wait()

    async def begin(self, **_rest):
        await self.start_async("work", self.forever())

    async def begin_assign(self, **_rest):
        await self.assign_async(self.forever())

    async def handle_async(self, name, result):
        HANDLED.append((name, result.state))


class SlowToHandle(Component):
    """An operation that ends at once and a handle_async that does not."""

    class Meta:
        template_name = "todo/counter.html"

    async def quick(self) -> int:
        return 1

    async def begin(self, **_rest):
        await self.start_async("work", self.quick())

    async def handle_async(self, name, result):
        HANDLED.append((name, "handling"))
        await asyncio.Event().wait()
        HANDLED.append((name, "handled"))


class FakeOutbound:
    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        pass

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


def _consumer() -> WireviewConsumer:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    consumer.outbound = FakeOutbound()  # type: ignore[assignment]
    return consumer


@pytest.fixture(autouse=True)
def _reset():
    HANDLED.clear()
    yield
    HANDLED.clear()


async def _tasks_of(component: Component) -> list[asyncio.Task]:
    await asyncio.sleep(0)
    return [*component._async_tasks.values(), *component._assign_tasks]


@pytest.mark.parametrize("handler", ["begin", "begin_assign"])
async def test_leaving_cancels_the_tasks_a_component_started(handler):
    consumer = _consumer()
    component = consumer.repo.build("Slow", {"id": "s-1"})
    await getattr(component, handler)()
    tasks = await _tasks_of(component)
    assert tasks and not any(task.done() for task in tasks)

    await consumer.command_leave("s-1")
    await asyncio.sleep(0)

    assert all(task.done() for task in tasks)
    assert component._async_tasks == {} and component._assign_tasks == set()
    # Cancelled is not finished: handle_async hears nothing
    assert HANDLED == []


async def test_leaving_cancels_a_task_still_in_its_handle_async():
    # Past its operation the task no longer holds the name, so handle_async can
    # start it again (#147). It must still end with the component (#95)
    consumer = _consumer()
    component = consumer.repo.build("SlowToHandle", {"id": "s-1"})
    await component.begin()
    task = component._async_tasks["work"]
    await eventually(lambda: HANDLED)
    assert HANDLED == [("work", "handling")] and not task.done()

    await consumer.command_leave("s-1")
    await asyncio.sleep(0)

    assert task.done()
    assert component._async_tasks == {} and component._assign_tasks == set()
    assert HANDLED == [("work", "handling")]


async def test_a_disconnect_cancels_every_components_tasks():
    consumer = _consumer()
    components = [consumer.repo.build("Slow", {"id": f"s-{n}"}) for n in range(2)]
    for component in components:
        await component.begin()
    tasks = [task for component in components for task in await _tasks_of(component)]

    await consumer._call_leaving(components)
    await asyncio.sleep(0)

    assert len(tasks) == 2 and all(task.done() for task in tasks)


async def test_a_replaced_task_leaves_its_replacement_tracked():
    view = await mount(Slow)
    await view.call("begin")
    first = view.component._async_tasks["work"]
    # Running, so being cancelled runs its cleanup
    await asyncio.sleep(0)
    await view.call("begin")
    second = view.component._async_tasks["work"]
    # Let the first one finish being cancelled
    for _ in range(3):
        await asyncio.sleep(0)

    assert first.done() and not second.done()
    assert view.component._async_tasks == {"work": second}
    assert await view.component.cancel_async("work") is True


async def test_assign_async_holds_its_task_until_it_is_done():
    view = await mount(Slow)
    await view.call("begin_assign")
    (task,) = await _tasks_of(view.component)

    assert not task.done()
    view.component._cancel_async_tasks()
    await asyncio.sleep(0)
    assert task.done() and view.component._assign_tasks == set()


async def test_the_tasks_of_two_instances_are_their_own():
    first, second = await mount(Slow), await mount(Slow)
    await first.call("begin")

    assert "work" in first.component._async_tasks
    assert second.component._async_tasks == {} and second.component._assign_tasks == set()
    first.component._cancel_async_tasks()


async def test_a_task_cancelled_before_it_ran_leaves_no_unawaited_coroutine(recwarn):
    view = await mount(Slow)
    await view.call("begin")
    view.component._cancel_async_tasks()
    await asyncio.sleep(0)

    assert not [w for w in recwarn if "never awaited" in str(w.message)]
