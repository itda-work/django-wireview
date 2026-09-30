"""Keep a component's background work off it while a worker thread renders it (#138).

A live render reads properties and runs the template on a worker thread (#120),
and the event loop keeps running meanwhile. What the component started with
``start_async`` or ``assign_async`` runs on that loop: the operation itself,
the result landing in its ``AsyncResult``, ``handle_async``. A step of that work
which ran while the worker was halfway through the template changed the state
under the render: the root tag had signed one state and the body showed
another, a frame signed "loading" that displayed the failure.

A render on the loop never had this problem -- nothing else ran until it
returned. ``RenderGate`` gives that back to the work the component owns:
``rendering()`` marks a render in flight, and ``run(coro)`` drives ``coro`` one
step at a time, starting a step only while no render of the component is in
flight. A step is the code between two awaits, so no step overlaps a render.
The loop itself stays free: other components and other connections carry on,
and the ORM stays off it.

A render started by the waiting task itself is let through. A background step
that renders its own component (a stream item, or a broker that delivers at
once, as in tests) waits on that render, and the render's continuation is the
task's next step.

Renders are counted per task, not stacked. Two tasks mark renders -- the
consumer, and a step let through to render a stream item -- and theirs overlap
and end in any order. The gate is open only while no task but the current one
has a render in flight, and every render that ends wakes the waiting steps to
look again.

Work the gate does not drive -- a task the application creates itself -- is not
kept off the render. ``docs/features/async-operations.md`` says so.
"""

import asyncio
import types
import typing as t
from collections.abc import Coroutine, Generator, Iterator
from contextlib import contextmanager

T = t.TypeVar("T")


class RenderGate:
    """Lets a component's background work run only between its renders."""

    __slots__ = ("_renders", "_idle")

    def __init__(self) -> None:
        # How many renders each task has in flight
        self._renders: dict[asyncio.Task[t.Any] | None, int] = {}
        # Made by the first step that has to wait, and gone once a render ends:
        # every component has a gate, few ever hold anything at it
        self._idle: asyncio.Event | None = None

    @contextmanager
    def rendering(self) -> Iterator[None]:
        """Mark a render of the component as in flight, from its first read to its last."""
        task = asyncio.current_task()
        self._renders[task] = self._renders.get(task, 0) + 1
        try:
            yield
        finally:
            if self._renders[task] == 1:
                del self._renders[task]
            else:
                self._renders[task] -= 1
            # Any render ending may open the gate for a waiting step: each looks again
            if self._idle is not None:
                self._idle.set()
                self._idle = None

    async def run(self, coro: Coroutine[t.Any, t.Any, T]) -> T:
        """Await ``coro``, each of its steps started only between renders."""
        return await self._between_renders(coro)

    def _blocked(self) -> bool:
        if not self._renders:
            return False
        current = asyncio.current_task()
        return any(task is not current for task in self._renders)

    @types.coroutine
    def _between_renders(self, coro: Coroutine[t.Any, t.Any, T]) -> Generator[t.Any, t.Any, T]:
        value: t.Any = None
        error: BaseException | None = None
        while True:
            while self._blocked():
                if self._idle is None:
                    self._idle = asyncio.Event()
                try:
                    yield from self._idle.wait().__await__()
                except GeneratorExit:
                    coro.close()
                    raise
                except BaseException as e:
                    # Cancelled while a render is in flight: the operation hears
                    # it once the render is done, like any step
                    error = e
            try:
                step = coro.send(value) if error is None else coro.throw(error)
            except StopIteration as stop:
                return stop.value
            value, error = None, None
            try:
                value = yield step
            except GeneratorExit:
                coro.close()
                raise
            except BaseException as e:
                error = e
