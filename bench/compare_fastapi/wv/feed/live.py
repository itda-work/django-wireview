"""The feed both wireview paths draw: a list of the store's items, newest first (#178).

``Feed`` publishes a new item with ``Broadcast``: rendered once, written to every
connection. ``NotifiedFeed`` does what an application did before it: a notification,
and every connection's ``notification()`` renders the item and sends it to itself.
"""

from bench.compare_fastapi.store import FEED_SIZE, store
from wireview import Broadcast, Component, abroadcast


def _dom_id(item: dict) -> str:
    return f"items-{item['id']}"


class Feed(Component):
    class Meta:
        template_name = "feed/feed.html"
        subscriptions = {"feed"}

    async def joined(self):
        await self.stream("items", list(store.items), dom_id=_dom_id)

    async def post(self):
        item = store.insert()
        await Broadcast(Feed, "feed").stream_insert("items", item, at=0, limit=FEED_SIZE, dom_id=_dom_id).asend()


class NotifiedFeed(Component):
    class Meta:
        template_name = "feed/feed.html"
        subscriptions = {"feed-notified"}

    async def joined(self):
        await self.stream("items", list(store.items), dom_id=_dom_id)

    async def post(self):
        await abroadcast("feed-notified", id=store.insert()["id"])

    async def notification(self, channel: str, id: int):
        item = next(item for item in store.items if item["id"] == id)
        await self.stream_insert("items", item, at=0, limit=FEED_SIZE, dom_id=_dom_id)
