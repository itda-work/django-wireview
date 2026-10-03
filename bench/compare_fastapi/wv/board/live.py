from bench.compare_fastapi.store import store
from wireview import Component, abroadcast


class Board(Component):
    class Meta:
        template_name = "board/board.html"
        subscriptions = {"board"}

    @property
    def count(self):
        return store.count

    @property
    def announcement(self):
        return store.announcement

    @property
    def items(self):
        return store.items

    async def increment(self):
        store.increment()

    async def insert(self):
        store.insert()

    async def announce(self):
        store.announce()
        await abroadcast("board")

    async def notification(self, channel: str, **kwargs):
        pass
