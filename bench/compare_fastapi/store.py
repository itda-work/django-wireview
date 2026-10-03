"""The data both implementations read and write: one in-memory store per server process.

Neither side touches a database, so the comparison is the two stacks and not two ORMs.
Every server starts from the same state, and a round always starts a fresh server.
"""

from collections import deque

FEED_SIZE = 50


def _item(n: int) -> dict:
    return {"id": n, "name": f"item {n}", "qty": n % 7, "done": n % 3 == 0}


class Store:
    def __init__(self) -> None:
        self.count = 0
        self.announcement = 0
        # Newest first, at most FEED_SIZE: an insert at the front drops the last item
        self.items: deque[dict] = deque((_item(n) for n in reversed(range(FEED_SIZE))), maxlen=FEED_SIZE)
        self._next = FEED_SIZE

    def increment(self) -> int:
        self.count += 1
        return self.count

    def insert(self) -> dict:
        item = _item(self._next)
        self._next += 1
        self.items.appendleft(item)
        return item

    def announce(self) -> int:
        self.announcement += 1
        return self.announcement

    def snapshot(self) -> dict:
        return {"count": self.count, "announcement": self.announcement, "items": list(self.items)}


store = Store()
