from wireview import Component
from wireview.schemas import ModelAction

from .models import Bookmark


class XBookmarkList(Component):
    _template_name = "bookmarks/x-bookmark-list.html"
    _subscriptions = {"bookmarks.bookmark"}

    filter: str = "all"

    async def joined(self):
        await self._load_stream()

    async def _load_stream(self):
        qs = Bookmark.objects.all().order_by("-created_at")
        if self.filter == "unread":
            qs = qs.filter(is_read=False)
        await self.stream("bookmarks", qs, limit=100)

    def _visible(self, bookmark: Bookmark) -> bool:
        return self.filter != "unread" or not bookmark.is_read

    async def add(self, title: str = "", url: str = ""):
        title = title.strip()
        url = url.strip()
        if not title or not url:
            return
        # No stream_insert here: the model signal reaches mutation() on every
        # connection including this one, and inserting in both places puts the
        # item in the list twice.
        await Bookmark.objects.acreate(title=title, url=url)

    async def toggle_read(self, bookmark_id: int):
        bookmark = await Bookmark.objects.aget(pk=bookmark_id)
        bookmark.is_read = not bookmark.is_read
        await bookmark.asave()

    async def delete(self, bookmark_id: int):
        await Bookmark.objects.filter(pk=bookmark_id).adelete()

    async def set_filter(self, filter: str):
        self.filter = filter
        await self._load_stream()

    async def mutation(self, channel, action, instance):
        dom_id = f"bookmarks-{instance.pk}"

        if action == ModelAction.DELETED:
            await self.stream_delete("bookmarks", dom_id)
            return

        if action == ModelAction.CREATED:
            if self._visible(instance):
                await self.stream_insert("bookmarks", instance, at=0)
            return

        # Updated: stream_insert does not replace an item that is already in the
        # DOM under the same id, it adds a second one. Remove it first.
        await self.stream_delete("bookmarks", dom_id)
        if self._visible(instance):
            await self.stream_insert("bookmarks", instance, at=0)
