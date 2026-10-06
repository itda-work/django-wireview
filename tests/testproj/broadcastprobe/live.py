"""A feed whose items, hook events and JS commands reach every open page by ``Broadcast`` (#178).

tests/test_broadcast_e2e.py opens the page in several browser contexts. ``POSTS``
stands in for the table: ``joined()`` reads it, and a page that reconnects gets
from it what it missed. ``BroadcastLookalike`` draws a list of the same name and
hears the same topic, and is not the target. ``BroadcastFailing`` is a target whose
join fails, so its page gets nothing for it.
"""

from dataclasses import dataclass

from wireview import JS, Broadcast, Component

TOPIC = "broadcastprobe"


@dataclass
class Post:
    pk: int
    text: str


#: The feed's rows, oldest first
POSTS: list[Post] = []


def reset() -> None:
    POSTS.clear()


class BroadcastFeed(Component):
    class Meta:
        template_name = "broadcastprobe/feed.html"
        subscriptions = {TOPIC}

    async def joined(self):
        await self.stream("items", list(reversed(POSTS)))

    async def post(self, text: str = "", **_rest):
        post = Post(max((p.pk for p in POSTS), default=0) + 1, text)
        POSTS.append(post)
        await Broadcast(BroadcastFeed, TOPIC).stream_insert("items", post, at=0).asend()
        await Broadcast(BroadcastFailing, TOPIC).stream_insert("items", post, at=0).asend()

    async def remove(self, pk: int = 0, **_rest):
        POSTS[:] = [p for p in POSTS if p.pk != pk]
        await Broadcast(BroadcastFeed, TOPIC).stream_delete("items", pk).asend()

    async def ping(self, **_rest):
        await (
            Broadcast(BroadcastFeed, TOPIC)
            .push_event("pinged", {"posts": len(POSTS)})
            .js(JS().add_class("[data-testid=flag]", "flagged"))
            .asend()
        )


class BroadcastLookalike(Component):
    class Meta:
        template_name = "broadcastprobe/lookalike.html"
        subscriptions = {TOPIC}


class BroadcastFailing(Component):
    class Meta:
        template_name = "broadcastprobe/failing.html"
        subscriptions = {TOPIC}

    async def joined(self):
        raise RuntimeError("the failing feed's joined went wrong")
