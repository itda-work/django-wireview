"""``testproj.waiting.eventually`` says why the wait failed, not only that it did (#148)."""

import asyncio

import pytest
from testproj.waiting import eventually

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_the_exception_of_the_task_it_watches_is_raised_at_once():
    async def work():
        await asyncio.sleep(0)
        raise ValueError("the cause")

    task = asyncio.create_task(work())

    # Without the task the wait runs out and says only that nothing happened
    with pytest.raises(ValueError, match="the cause"):
        await eventually(lambda: False, task=task, timeout=1)


async def test_a_task_that_ends_well_does_not_end_the_wait():
    done = []

    async def work():
        await asyncio.sleep(0)

    task = asyncio.create_task(work())
    await task
    asyncio.get_running_loop().call_later(0.05, done.append, True)

    assert await eventually(lambda: done, task=task) == [True]


async def test_a_cancelled_task_is_not_a_cause():
    """Cancelling is how tests stop background work; it says nothing about the condition."""
    task = asyncio.create_task(asyncio.sleep(10))
    task.cancel()
    await asyncio.sleep(0)

    with pytest.raises(AssertionError, match="not true within"):
        await eventually(lambda: False, task=task, timeout=0.05)
