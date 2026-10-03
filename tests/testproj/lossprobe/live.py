"""Broadcasts past the channel layer's capacity (#168).

tests/test_broadcast_loss_e2e.py drives it. ``LossSender.burst`` publishes a run
of notifications while ``LossBox`` is still handling the first one: its handler
waits for ``GATE``, which the test opens once the burst is out, and a connection
reads its next message only after the last one is handled -- so the receiver's
queue fills and the layer has to drop what does not fit.

``received`` counts the notifications that arrived, as a component that adds up
what it hears does. ``published`` is read from the source of truth at render
time, as a component that reloads what it shows does. ``HEARD`` keeps the
numbers that arrived, in order, for the test to see which ones the layer kept.
"""

import asyncio
import threading

from wireview import Component

#: What every burst has published so far: the source of truth.
PUBLISHED = {"n": 0}

#: The numbers the receiver heard, in order.
HEARD: list[int] = []

#: Holds the receiver in its first notification until the test opens it.
GATE = threading.Event()


class LossBox(Component):
    class Meta:
        template_name = "lossprobe/box.html"
        subscriptions = {"lossprobe"}

    received: int = 0
    last: int = 0

    @property
    def published(self) -> int:
        return PUBLISHED["n"]

    async def notification(self, channel, n: int = 0, **kwargs):
        self.received += 1
        self.last = n
        HEARD.append(n)
        while not GATE.is_set():
            await asyncio.sleep(0.01)

    async def refresh(self, **_rest):
        pass


class LossSender(Component):
    class Meta:
        template_name = "lossprobe/sender.html"

    sent: int = 0

    async def burst(self, count: int = 1, **_rest):
        for _ in range(int(count)):
            PUBLISHED["n"] += 1
            await self.broadcast("lossprobe", n=PUBLISHED["n"])
        self.sent += int(count)
