"""Waiting for something a background task will do, in async tests (#143).

A fixed ``asyncio.sleep`` guesses how long the work takes. The guess holds on a
laptop and not on a busy CI runner, where the test then fails for no fault of
the code. ``eventually`` waits for the result itself: it checks often and gives
up only after a bound far past any honest delay.

It is not for timers. A test of a debounce, a throttle or a timeout measures
time on purpose and keeps its sleep.
"""

from __future__ import annotations

import asyncio
import typing as t

T = t.TypeVar("T")


async def eventually(condition: t.Callable[[], T], *, timeout: float = 5.0, interval: float = 0.01) -> T:
    """Return ``condition()`` once it is truthy; fail if it is not within ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not (value := condition()):
        if loop.time() > deadline:
            raise AssertionError(f"not true within {timeout}s: {getattr(condition, '__name__', condition)}")
        await asyncio.sleep(interval)
    return value
