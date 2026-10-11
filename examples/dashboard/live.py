"""
Dashboard App Components

This module demonstrates wireview's advanced patterns:
- AsyncResult for loading states
- Complex component composition with nested components
- Streams API for activity feed
- Tab navigation with conditional rendering
- URL state management with wire.params
"""

import asyncio
from datetime import datetime
from uuid import UUID

from django.db.models import Q

from wireview import AsyncResult, Component, ModelAction

from .models import Activity, Stat

#: How many activities the feed opens on, and how many each "Load More" adds
PAGE_SIZE = 10


def _activities_before(before: tuple[datetime, UUID] | None):
    """The activities older than ``before``, newest first: a page, and one more to tell if there are more.

    ``before`` is the oldest activity the page shows, as (created_at, id). A
    cursor, not an offset: an activity added on top in real time, or deleted,
    moves every offset by one, and the next page repeats or skips one. Two
    activities can share a created_at, so the id breaks the tie.
    """
    activities = Activity.objects.order_by("-created_at", "-id")
    if before is not None:
        at, pk = before
        activities = activities.filter(Q(created_at__lt=at) | Q(created_at=at, id__lt=pk))
    return activities[: PAGE_SIZE + 1]


class XDashboard(Component):
    """
    Main dashboard container component.

    Demonstrates:
    - Multiple nested child components
    - Tab-based navigation
    - URL state management via wire.params
    - Conditional rendering based on active tab
    """

    class Meta:
        template_name = "dashboard/dashboard.html"

    active_tab: str = "overview"
    date_range: str = "7d"

    async def joined(self):
        """
        Initialize dashboard state from URL parameters.

        wire.params provides access to URL query parameters,
        enabling bookmarkable state.
        """
        self.active_tab = self.wire.params.get("tab", "overview")
        self.date_range = self.wire.params.get("range", "7d")

    async def change_tab(self, tab: str):
        """
        Switch dashboard tabs.

        Updates both component state and URL for bookmarkability.
        """
        self.active_tab = tab
        self.wire.params["tab"] = tab

    async def change_date_range(self, range: str):
        """Update date range filter."""
        self.date_range = range
        self.wire.params["range"] = range


class XStatCard(Component):
    """
    Individual stat card with async loading state.

    Demonstrates:
    - AsyncResult for loading/success/error states
    - assign_async() for deferred data loading
    - Model subscriptions for real-time updates
    - Manual refresh capability
    """

    class Meta:
        template_name = "dashboard/stat_card.html"
        subscriptions = {"dashboard.stat"}

    # Name of the stat to load
    stat_name: str

    # AsyncResult provides loading, ok, failed states
    # Type hint for what the result will contain (Stat or None if not found)
    data: AsyncResult[Stat | None] | None = None

    async def joined(self):
        """
        Load stat data asynchronously.

        assign_async() returns immediately with a loading state,
        then updates to success/error when the coroutine completes.
        """
        self.data = await self.assign_async(self._load_stat())

    async def _load_stat(self) -> Stat | None:
        """
        Simulate loading stat with potential delay.

        In production, this might be an API call or complex query.
        """
        # Simulate network delay for demo purposes
        await asyncio.sleep(0.3)
        try:
            return await Stat.objects.aget(name=self.stat_name)
        except Stat.DoesNotExist:
            return None

    async def mutation(
        self,
        channel: str,
        action: ModelAction,
        instance: Stat,
    ):
        """Update when this stat changes in the database."""
        if instance.name == self.stat_name:
            # Update with new value directly (no loading state)
            self.data = AsyncResult.success(instance)

    async def refresh(self):
        """
        Manual refresh with loading state.

        Shows loading indicator while fetching fresh data.
        """
        self.data = await self.assign_async(self._load_stat())


class XActivityFeed(Component):
    """
    Activity feed using Streams for efficient updates.

    Demonstrates:
    - Streams API for large list handling
    - stream_insert() for prepending new items
    - Load more pagination
    - Model subscriptions for real-time updates
    """

    class Meta:
        template_name = "dashboard/activity_feed.html"
        subscriptions = {"dashboard.activity"}

    # The oldest activity the page was sent: where the next page starts. The
    # items themselves are the page's; the server keeps none of them
    oldest_at: datetime | None = None
    oldest_id: UUID | None = None
    is_loading: bool = False
    has_more: bool = True

    async def joined(self):
        """Load initial activities using Streams."""
        latest = [a async for a in _activities_before(None)]
        await self.stream("activities", latest[:PAGE_SIZE])
        self._moved_to(latest)

    async def mutation(
        self,
        channel: str,
        action: ModelAction,
        instance: Activity,
    ):
        """Handle new activities - prepend to the stream."""
        if action == ModelAction.CREATED:
            # Insert at position 0 (prepend)
            await self.stream_insert("activities", instance, at=0)

    async def load_more(self):
        """
        Load older activities (pagination).

        Demonstrates loading state and appending to streams.
        """
        self.is_loading = True
        await self.send_render()  # Show loading state

        before = None if self.oldest_at is None else (self.oldest_at, self.oldest_id)
        older = [a async for a in _activities_before(before)]

        # Appended (at=-1) in one operation; reset=False keeps what the page shows
        await self.stream("activities", older[:PAGE_SIZE], reset=False)

        self._moved_to(older)
        self.is_loading = False

    def _moved_to(self, page: list[Activity]) -> None:
        """Remember where ``page`` (a page and one more, newest first) ends."""
        shown = page[:PAGE_SIZE]
        if shown:
            self.oldest_at, self.oldest_id = shown[-1].created_at, shown[-1].id
        self.has_more = len(page) > PAGE_SIZE

    async def clear_activity(self, activity_id: str):
        """
        Remove an activity from the feed.

        Demonstrates stream_delete() for removing items.
        """
        await self.stream_delete("activities", f"activities-{activity_id}")
