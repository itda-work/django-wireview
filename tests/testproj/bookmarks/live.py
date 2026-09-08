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
        bookmark = await Bookmark.objects.acreate(title=title, url=url)
        if self._visible(bookmark):
            await self.stream_insert("bookmarks", bookmark, at=0)

    async def toggle_read(self, bookmark_id: int):
        bookmark = await Bookmark.objects.aget(pk=bookmark_id)
        bookmark.is_read = not bookmark.is_read
        await bookmark.asave()
        if self._visible(bookmark):
            await self.stream_insert("bookmarks", bookmark)
        else:
            await self.stream_delete("bookmarks", f"bookmarks-{bookmark.pk}")

    async def delete(self, bookmark_id: int):
        await Bookmark.objects.filter(pk=bookmark_id).adelete()
        await self.stream_delete("bookmarks", f"bookmarks-{bookmark_id}")

    async def set_filter(self, filter: str):
        self.filter = filter
        await self._load_stream()

    async def mutation(self, channel, action, instance):
        if action == ModelAction.CREATED:
            if self._visible(instance):
                await self.stream_insert("bookmarks", instance, at=0)
        elif action == ModelAction.DELETED:
            await self.stream_delete("bookmarks", f"bookmarks-{instance.pk}")
        else:
            if self._visible(instance):
                await self.stream_insert("bookmarks", instance)
            else:
                await self.stream_delete("bookmarks", f"bookmarks-{instance.pk}")
