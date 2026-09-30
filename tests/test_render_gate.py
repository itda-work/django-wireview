"""A component's background work runs between its renders, never during one (#138).

A live render reads the component on a worker thread (#120) while the loop runs
on. ``RenderGate`` holds each step of the work ``start_async``/``assign_async``
started until no render of the component is in flight. These tests drive the
gate with the loop alone; tests/test_start_async.py shows it through the
consumer, with a render thread slowed the way a CI runner slowed it.
"""

import asyncio

import pytest

from wireview.core.render_gate import RenderGate

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def render(gate: RenderGate, log: list[str], hold: asyncio.Event) -> None:
    with gate.rendering():
        log.append("render starts")
        await hold.wait()
        log.append("render ends")


async def test_a_step_waits_for_the_render_in_flight():
    gate, log, hold = RenderGate(), [], asyncio.Event()

    async def work():
        log.append("step 1")
        await asyncio.sleep(0)
        log.append("step 2")
        return "done"

    rendering = asyncio.create_task(render(gate, log, hold))
    await asyncio.sleep(0)
    task = asyncio.create_task(gate.run(work()))
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["render starts"]

    hold.set()
    assert await task == "done"
    await rendering
    assert log == ["render starts", "render ends", "step 1", "step 2"]


async def test_a_render_that_starts_between_two_steps_holds_the_second():
    gate, log, hold = RenderGate(), [], asyncio.Event()
    rendering: list[asyncio.Task] = []

    async def work():
        log.append("step 1")
        rendering.append(asyncio.create_task(render(gate, log, hold)))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        log.append("step 2")

    task = asyncio.create_task(gate.run(work()))
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["step 1", "render starts"]

    hold.set()
    await task
    assert log == ["step 1", "render starts", "render ends", "step 2"]


async def test_the_loop_is_not_held():
    # Only the gated work waits: another component, another connection runs on
    gate, log, hold = RenderGate(), [], asyncio.Event()
    rendering = asyncio.create_task(render(gate, log, hold))
    await asyncio.sleep(0)

    async def elsewhere():
        log.append("elsewhere")

    await asyncio.wait_for(elsewhere(), 1)
    assert log == ["render starts", "elsewhere"]
    hold.set()
    await rendering


async def test_a_render_by_the_work_itself_goes_through():
    # A step that renders its own component (a broker that delivers at once)
    # waits on that render; holding its next step for it would never end
    gate, log, hold = RenderGate(), [], asyncio.Event()

    async def work():
        await render(gate, log, hold)
        log.append("after")

    task = asyncio.create_task(gate.run(work()))
    await asyncio.sleep(0)
    hold.set()
    for _ in range(20):
        await asyncio.sleep(0)
    stalled = not task.done()
    if stalled:
        # Free it by hand: cancelling a held task waits for the render, which is its own
        gate._renders.clear()
        if gate._idle is not None:
            gate._idle.set()
    await task
    assert not stalled
    assert log == ["render starts", "render ends", "after"]


async def test_cancelled_while_held_the_work_hears_it_after_the_render():
    gate, log, hold = RenderGate(), [], asyncio.Event()

    async def work():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            log.append("cleanup")
            raise

    task = asyncio.create_task(gate.run(work()))
    await asyncio.sleep(0)
    rendering = asyncio.create_task(render(gate, log, hold))
    await asyncio.sleep(0)
    task.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["render starts"]

    hold.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await rendering
    assert log == ["render starts", "render ends", "cleanup"]
    assert task.cancelled()


async def test_an_exception_from_the_work_reaches_the_awaiter():
    gate = RenderGate()

    async def work():
        await asyncio.sleep(0)
        raise LookupError("offline")

    with pytest.raises(LookupError, match="offline"):
        await gate.run(work())


async def test_a_render_that_raises_leaves_the_gate_open():
    # A stream item whose template raises ends the render with an exception: the
    # render is over all the same, and the work after it must not wait for good
    gate = RenderGate()

    async def broken():
        with gate.rendering():
            await asyncio.sleep(0)
            raise LookupError("template broke")

    with pytest.raises(LookupError):
        await asyncio.create_task(broken())

    async def work():
        return "done"

    task = asyncio.create_task(gate.run(work()))
    done, _ = await asyncio.wait([task], timeout=1)
    if not done:
        # Free it by hand: cancelling a held task waits for a render that is gone
        gate._renders.clear()
        if gate._idle is not None:
            gate._idle.set()
        await task
    assert done, "the work waited for a render that had raised"
    assert gate._renders == {}


async def test_renders_by_two_tasks_that_end_out_of_order_hold_the_work_until_both_end():
    # Renders by different tasks need not end last-in, first-out: the gate opens
    # only once none is left, and not for good once the first one ends
    gate, log = RenderGate(), []
    first, second = asyncio.Event(), asyncio.Event()
    a = asyncio.create_task(render(gate, log, first))
    await asyncio.sleep(0)
    b = asyncio.create_task(render(gate, log, second))
    await asyncio.sleep(0)

    async def work():
        log.append("step")

    task = asyncio.create_task(gate.run(work()))
    first.set()
    await a
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["render starts", "render starts", "render ends"]

    second.set()
    await b
    await asyncio.wait_for(task, 1)
    assert log[-1] == "step"
    # And the gate stays open for the work that comes after
    await asyncio.wait_for(gate.run(work()), 1)


async def test_a_render_by_the_work_itself_overlapping_another_render_holds_its_next_step():
    # A gated step renders its component (a stream item) and a render by another
    # task (the consumer's, for a click) starts meanwhile. The worker thread
    # finishes the work's render first, but the work picks up only once the
    # other render is done; then the work and the work after it run
    gate, log = RenderGate(), []
    own, other = asyncio.Event(), asyncio.Event()

    async def work():
        await render(gate, log, own)
        log.append("next step")
        return "done"

    task = asyncio.create_task(gate.run(work()))
    await asyncio.sleep(0)
    consumer = asyncio.create_task(render(gate, log, other))
    await asyncio.sleep(0)
    own.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["render starts", "render starts"]

    other.set()
    await consumer
    assert await asyncio.wait_for(task, 1) == "done"
    assert log == ["render starts", "render starts", "render ends", "render ends", "next step"]

    async def later():
        return "later"

    assert await asyncio.wait_for(gate.run(later()), 1) == "later"


async def test_cancelled_while_waiting_at_the_gate_the_work_hears_it_after_the_render():
    # The work already waits for a render to end, not for its own await, when
    # the cancellation comes: its cleanup still runs after the render
    gate, log, hold, go = RenderGate(), [], asyncio.Event(), asyncio.Event()

    async def work():
        try:
            log.append("step 1")
            await go.wait()
            log.append("step 2")
        except asyncio.CancelledError:
            log.append("cleanup")
            raise

    task = asyncio.create_task(gate.run(work()))
    await asyncio.sleep(0)
    rendering = asyncio.create_task(render(gate, log, hold))
    await asyncio.sleep(0)
    # The await is over while the render is in flight: the work now waits at the gate
    go.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["step 1", "render starts"]

    task.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["step 1", "render starts"]

    hold.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await rendering
    assert log == ["step 1", "render starts", "render ends", "cleanup"]
    assert task.cancelled()
