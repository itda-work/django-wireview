"""
Chat App Components (level 4)

The chat example, where the server decides who is speaking:
- The author of a message, and the name in the online list, come from
  ``self.user`` -- the connection's signed-in user. No handler takes a name and
  no state field holds one.
- Every component here lives inside the ``chat_lv4`` live_session
  (``Meta.live_sessions``), whose hook refuses a user who is not a member of
  the component's room, on every path that mounts it (``access.py``).
- Leaving a room ends what the membership's open pages hear of it: told on a
  topic of their own, each component asks again and gives up the room's
  topics on the server, and the page is sent back to the room list.

Level 3 adds that a stream is more than a list things are appended to:
- The list opens on the latest messages; scrolling up to the oldest one loads
  the page before it (``wire-viewport-top``), until the room's first message.
- An author edits or deletes their own messages, and every open page of the
  room shows it: a Broadcast replaces the item where a page shows it, or
  deletes it.

Level 4 adds news that is one person's: the room list shows, beside each room,
how many messages its user has not read.
- A membership remembers the newest message its user has seen (a cursor, as
  the list's). A room page that is open reads what arrives: it moves the
  cursor when it joins and whenever the room gets a new message.
- Unlike a message, which goes to every page of the room as the same patch,
  a badge is different for every member. So the news goes on a topic of each
  user's own, and says only "count again": the room list asks the database,
  in one query for all its rooms.

The rest is the chat example's:
- Streams API for efficient message list updates
- Broadcast: a new message rendered once and put on every open page of the room
- PresenceMixin for typing indicators with auto-timeout
- PresenceTrackerMixin for tracking online users
- Real-time presence tracking with sync support
- leaving() lifecycle hook for disconnect handling
"""

import typing as t
from datetime import UTC, datetime
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import Count, DateTimeField, Exists, F, OuterRef, Q, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from wireview import (
    JS,
    Broadcast,
    Component,
    PresenceConfig,
    PresenceMixin,
    PresenceTrackerMixin,
    abroadcast,
    broadcast,
)

from .access import ais_member
from .models import Membership, Message, Room


def membership_topic(room_id, user_id) -> str:
    """Told when one user's membership of one room ends. Their components of that room hear it."""
    return f"chat_lv4.membership.{room_id}.{user_id}"


def user_topic(user_id) -> str:
    """One user's news: a room of theirs got a message, lost one, was read or joined or left.

    Only that user's components listen. A notice here says which room and
    what happened, never a count: the count is each user's own, and the
    listener reads it from the database.
    """
    return f"chat_lv4.user.{user_id}"


async def tell_members(room: Room, sender, event: str) -> None:
    """Tell the room's members, the sender aside, that ``event`` happened in it.

    Called after the write is committed (outside a transaction an async write
    commits at once): the members' lists count again, and must see it. Sent to
    who is a member now -- someone who left hears nothing of the room.
    """
    members = Membership.objects.filter(room=room).exclude(user_id=sender.pk).values_list("user_id", flat=True)
    async for user_id in members:
        await abroadcast(user_topic(user_id), room=str(room.id), event=event)


def end_membership(room: Room, user) -> None:
    """
    Take ``user`` out of ``room``, and tell their open pages of it.

    The notice goes out once the delete is committed. A component that hears
    it asks ``is_member`` again, and must get the answer after the delete.
    broadcast() already waits for that: in a transaction (the view's
    ATOMIC_REQUESTS) it publishes on commit, outside one at once
    (docs/features/broadcast.md). abroadcast() publishes at once, inside a
    transaction or not, so an async caller must not send it before the commit.
    """
    Membership.objects.filter(room=room, user_id=user.pk).delete()
    broadcast(membership_topic(room.id, user.pk))
    # And their room lists, which drop the room's link and badge
    broadcast(user_topic(user.pk), room=str(room.id), event="left")


# The components of the room page share one rule after they mount. The boundary's
# hook let them in and is not asked again while the page stays open, and a redirect
# only asks the browser to go. So each one also listens on this user's membership
# topic, and when told asks is_member again: the notice says "ask", the database
# says what is true. If the answer is no it sets _left, and get_subscriptions()
# leaves out every topic of the room. The session reads get_subscriptions() of each
# component again after it delivered a notification, and unsubscribes the
# connection from the topics no component asks for any more
# (docs/features/live-session.md#세션-백엔드가-정하는-한계). From then on
# the socket gets none of the room's messages or presence, whether the page went or
# not.


class Lv4ChatRoom(PresenceMixin, Component):
    """
    Main chat room component.

    Demonstrates:
    - The author and the presence name read from self.user, never from the browser
    - A membership check in the handlers: events are not authorized again
    - Nested components (Lv4MessageList, Lv4OnlineUsers)
    - PresenceMixin for typing indicators with auto-timeout
    - leaving() lifecycle hook for disconnect cleanup
    - Event modifiers (keypress.enter.prevent, input.debounce)
    - skip_render() for optimization
    """

    class Meta:
        template_name = "chat_lv4/room_component.html"
        live_sessions = {"chat_lv4"}
        # Presence configuration: 3 second typing timeout
        presence = PresenceConfig(typing_timeout=3.0)

    # Room is a Django model - serialized by PK automatically. There is no
    # username field: a field is state the browser holds, and the name is not
    # the browser's to give.
    room: Room
    # Set when this user's membership of the room ends while the page is open
    _left: bool = False

    def get_subscriptions(self) -> set[str]:
        return set() if self._left else {membership_topic(self.room.id, self.user.pk)}

    def _presence_topic(self) -> str:
        """Return room-specific topic for presence channel."""
        return f"chat_lv4.room.{self.room.id}"

    def _presence_user_id(self) -> str:
        """The user's primary key: it comes from the account, not from the browser."""
        return str(self.user.pk)

    def _presence_username(self) -> str:
        """Return display username."""
        return self.user.get_username()

    async def joined(self):
        """
        Called when component mounts.

        Broadcast presence to notify other users in the room.
        Uses PresenceMixin.presence_join() for pending-aware broadcast.
        """
        await self.presence_join()

    async def leaving(self):
        """
        Called when WebSocket disconnects.

        Broadcast "left" action so other users can remove this user
        from their online users list. Cleans up typing timeout.

        A page that left the room said so in _leave(). Saying it again would
        take the user off the list of a page they opened since.
        """
        if not self._left:
            await self.presence_leave()

    async def notification(self, channel: str, **kwargs):
        """This user's membership of the room may have ended, on this page or another."""
        if channel == membership_topic(self.room.id, self.user.pk):
            if not await ais_member(self.user, self.room):
                await self._leave()
            self.skip_render()
            return
        await super().notification(channel, **kwargs)

    async def send_message(self, content: str):
        """
        Send a new message to the room, written by this connection's user.

        The message is rendered once, here, and every page with this room's
        message list open -- this one too -- puts it in its list: a Broadcast
        runs no code on the pages it reaches. Clears the input field via
        push_js() and the typing indicator; nothing of this component changes,
        so skip_render().

        The page was let in when the user was a member; that may have ended
        since, so the handler asks again before it writes.

        The other members' badges are not in that patch: each is their own.
        Each member is told on their own topic, and counts again.
        """
        content = content.strip()
        if content and await ais_member(self.user, self.room):
            message = await Message.objects.acreate(room=self.room, author=self.user, content=content)
            await Broadcast(Lv4MessageList, room_topic(self.room)).stream_insert("messages", message, at=0).asend()
            await tell_members(self.room, self.user, "message")
        # Clear typing indicator when sending message
        await self.presence_set_typing(False)
        # Clear the input field
        await self.push_js(JS().set_value("input[name=content]", ""))
        self.skip_render()

    async def on_typing(self):
        """
        Called when user types in the input field.

        Uses PresenceMixin.presence_set_typing() which handles:
        - Broadcasting typing state to other users
        - Auto-timeout after 3 seconds of inactivity
        - Debouncing (handled by template {% on 'input.debounce.100' %})

        Only a member's typing is shown to the room. The notice that the
        membership ended may not have arrived, so the handler asks.
        """
        if await ais_member(self.user, self.room):
            await self.presence_set_typing(True)
        else:
            self.skip_render()

    async def leave_room(self):
        """Stop being a member. This user's pages of the room, this one included, go to the room list."""
        await sync_to_async(end_membership)(self.room, self.user)
        await self._leave()

    async def _leave(self):
        """Hear nothing more of the room, leave its online list, and send the page to the room list."""
        self._left = True
        await self.presence_leave()
        await self.wire.redirect_to(reverse("chat_lv4:index"))


def room_topic(room: Room) -> str:
    """Where a room's new messages are broadcast. Only that room's message lists hear it."""
    return f"chat_lv4.room.{room.id}"


#: How many messages the list opens on, and how many each scroll to the top loads
PAGE_SIZE = 30


def _history(room: Room, before: tuple[datetime, UUID] | None):
    """The room's messages older than ``before``, newest first: one page, and one more to tell if there are more.

    ``before`` is the oldest message a page shows, as (created_at, id). Two
    messages can share a created_at, so the id breaks the tie: "older" is the
    pair, compared the way the list is ordered. Comparing the time alone loses
    the messages that share it with the cursor, or shows them twice.

    The room is the query's, whatever ``before`` says: a cursor names a place
    in this room's history and cannot reach another room's.
    """
    messages = Message.objects.filter(room=room).select_related("author").order_by("-created_at", "-id")
    if before is not None:
        at, pk = before
        messages = messages.filter(Q(created_at__lt=at) | Q(created_at=at, id__lt=pk))
    return messages[: PAGE_SIZE + 1]


async def _page_of(history, read: list[Message]) -> t.AsyncIterator[Message]:
    """The page of ``history`` (a page and one more), each message kept in ``read`` as it is read.

    An async iterable rather than a list: ``stream()`` holds the list's
    Broadcast patches from before it reads one. A list read first would leave
    the read uncovered, and a message deleted meanwhile could come back with the page.
    """
    async for message in history:
        read.append(message)
        if len(read) <= PAGE_SIZE:
            yield message


class Lv4MessageList(Component):
    """
    Message list using Streams for efficient updates.

    Demonstrates:
    - stream() for initial list population
    - a Broadcast from Lv4ChatRoom.send_message for each new message: no code
      of this component runs for it
    - get_subscriptions() narrowing the topic to the room
    - Efficient memory usage with large lists
    - Template pattern with _item.html
    - wire-viewport-top loading older messages a page at a time, from a cursor
      in the signed state
    - Broadcast stream_insert(update_only=True) and stream_delete for an edit
      and a delete: every page of the room shows it, wherever it scrolled to
    - the user's read cursor, moved while the page is open: when it joins, and
      when its user's topic says the room has a new message

    The list is newest first in the DOM and drawn bottom up
    (flex-direction: column-reverse), so a new message goes in at=0 and the
    list stays scrolled to the newest one without a scroll command per page.
    An older page goes in at=-1: the end of the DOM, the top of the screen.
    """

    class Meta:
        template_name = "chat_lv4/message_list.html"
        live_sessions = {"chat_lv4"}

    room: Room
    # The oldest message the page shows, where the next older page starts. In
    # the signed state like every field: the browser holds it and cannot change it
    oldest_at: datetime | None = None
    oldest_id: UUID | None = None
    # Nothing older is left: the list shows the room's first message
    reached_start: bool = False
    # The message this user is editing, and its text when they began
    editing: UUID | None = None
    draft: str = ""
    # Set when this user's membership of the room ends while the page is open
    _left: bool = False

    def get_subscriptions(self) -> set[str]:
        if self._left:
            return set()
        return {room_topic(self.room), membership_topic(self.room.id, self.user.pk), user_topic(self.user.pk)}

    async def notification(self, channel: str, **kwargs):
        """This user's membership of the room may have ended: ask, and stop hearing the room if it did.

        On the user's own topic, a new message in this room: the page shows it,
        so the user has read it. Every other notice there is for the room list.
        """
        if channel == membership_topic(self.room.id, self.user.pk):
            self._left = not await ais_member(self.user, self.room)
        elif channel == user_topic(self.user.pk):
            if (kwargs.get("room"), kwargs.get("event")) == (str(self.room.id), "message"):
                await self._mark_read()
        self.skip_render()

    async def joined(self):
        """
        Load initial messages using Streams.

        stream() efficiently renders each item individually and
        sends them to the client without keeping all HTML in memory.

        The latest page only; the cursor starts at its oldest message. Every
        join starts here again, a reconnect too: the reset replaces whatever
        older pages the list had loaded.
        """
        latest = [message async for message in _history(self.room, None)]
        await self.stream("messages", latest[:PAGE_SIZE])
        self._moved_to(latest)
        # The page shows the room's newest message: the user has read it
        await self._mark_read(latest[0] if latest else None)

    async def _mark_read(self, newest: Message | None = None) -> None:
        """
        Move this user's read cursor to ``newest`` (the room's newest message by
        default), and tell their room lists.

        Only forward: a page that read less (an older notice, a slower tab)
        cannot move it back, so the update says "where the cursor is before
        this". And only their own membership of this room: no row, nothing
        moves -- a user who left reads nothing here.
        """
        if newest is None:
            newest = await Message.objects.filter(room=self.room).order_by("-created_at", "-id").afirst()
            if newest is None:
                return
        at, pk = newest.created_at, newest.id
        moved = (
            await Membership.objects.filter(room=self.room, user_id=self.user.pk)
            .filter(Q(read_at__isnull=True) | Q(read_at__lt=at) | Q(read_at=at, read_id__lt=pk))
            .aupdate(read_at=at, read_id=pk)
        )
        if moved:
            # Queued until the page's topics are subscribed when in joined(), at once otherwise
            await self.broadcast(user_topic(self.user.pk), room=str(self.room.id), event="read")

    async def load_older(self):
        """
        The page before the oldest message shown, put above it.

        Called by wire-viewport-top when the user scrolls up to it. The room's
        members only: the page was let in when the user was one, and the
        handler asks again as every handler here does.
        """
        if self.reached_start or self.oldest_at is None or not await ais_member(self.user, self.room):
            self.skip_render()
            return
        older: list[Message] = []
        # One operation for the page, in its order (newest first) under what the
        # list holds: reset=False keeps the list, and the page is one frame.
        # stream() reads the query itself, so an edit or a delete published
        # while it reads is written after the page, not undone by it
        await self.stream(
            "messages", _page_of(_history(self.room, (self.oldest_at, self.oldest_id)), older), at=-1, reset=False
        )
        self._moved_to(older)

    def _moved_to(self, page: list[Message]) -> None:
        """Remember where ``page`` (a page and one more, newest first) ends."""
        shown = page[:PAGE_SIZE]
        if shown:
            self.oldest_at, self.oldest_id = shown[-1].created_at, shown[-1].id
        self.reached_start = len(page) <= PAGE_SIZE

    async def _own_message(self, message_id: UUID | None) -> Message | None:
        """The message, if this user wrote it, in this room, and is still a member there.

        The buttons only show on the user's own messages, but the event can
        name any id: this is what decides.
        """
        if message_id is None or not await ais_member(self.user, self.room):
            return None
        return (
            await Message.objects.filter(id=message_id, room=self.room, author_id=self.user.pk)
            .select_related("author")
            .afirst()
        )

    async def edit_message(self, message_id: UUID):
        """Open the editor on one of the user's own messages. Any other id is ignored."""
        message = await self._own_message(message_id)
        if message is None:
            self.skip_render()
            return
        self.editing, self.draft = message.id, message.content

    async def save_edit(self, text: str):
        """
        Save the edit, and show it on every page of the room.

        The new version goes out as a Broadcast with the same DOM id: a page
        that shows the message replaces it where it stands. update_only=True,
        because a page that never loaded it (it scrolled less far) would
        otherwise insert it at the bottom, out of its place.
        """
        message = await self._own_message(self.editing)
        text = text.strip()
        if message is not None and text:
            message.content, message.edited_at = text, timezone.now()
            await message.asave(update_fields=["content", "edited_at"])
            await (
                Broadcast(Lv4MessageList, room_topic(self.room))
                .stream_insert("messages", message, update_only=True)
                .asend()
            )
        self.editing, self.draft = None, ""

    async def cancel_edit(self):
        self.editing, self.draft = None, ""

    async def delete_message(self, message_id: UUID):
        """Delete one of the user's own messages, from the room and from every page of it."""
        message = await self._own_message(message_id)
        if message is None:
            self.skip_render()
            return
        await message.adelete()
        await (
            Broadcast(Lv4MessageList, room_topic(self.room)).stream_delete("messages", f"messages-{message_id}").asend()
        )
        # An unread message gone is one fewer on the others' badges
        await tell_members(self.room, self.user, "deleted")
        if self.editing == message_id:
            self.editing, self.draft = None, ""
        else:
            self.skip_render()


class Lv4OnlineUsers(PresenceTrackerMixin, Component):
    """
    Display online users with typing indicators.

    Demonstrates:
    - PresenceTrackerMixin for tracking online users
    - Dynamic @property _subscriptions
    - Automatic notification() handling from mixin
    - Presence sync via request-response pattern
    """

    class Meta:
        template_name = "chat_lv4/online_users.html"
        live_sessions = {"chat_lv4"}

    room: Room
    # Set when this user's membership of the room ends while the page is open
    _left: bool = False

    def _presence_topic(self) -> str:
        """Return room-specific topic for presence channel."""
        return f"chat_lv4.room.{self.room.id}"

    def _presence_my_user_id(self) -> str:
        """The user's primary key, as Lv4ChatRoom announces it."""
        return str(self.user.pk)

    def get_subscriptions(self) -> set[str]:
        """Subscribe to room-specific presence channel, while a member."""
        if self._left:
            return set()
        return {self._presence_channel(), membership_topic(self.room.id, self.user.pk)}

    async def notification(self, channel: str, **kwargs):
        """The membership notice is this component's; the rest is presence, for the mixin."""
        if channel == membership_topic(self.room.id, self.user.pk):
            self._left = not await ais_member(self.user, self.room)
            self.skip_render()
            return
        await super().notification(channel, **kwargs)

    async def joined(self):
        """
        Register self in presence list when joining.

        Uses PresenceTrackerMixin.presence_track_self() which:
        - Adds current user to the presence list
        - Requests sync from other users
        """
        await self.presence_track_self(username=self.user.get_username())


#: Where the cursor of a room never opened is: before every message
EARLIEST = datetime.min.replace(tzinfo=UTC)


def unread_counts(user) -> dict[UUID, int]:
    """How many messages ``user`` has not read, by room: one query, however many rooms they are in.

    Unread is every message of a room the user is a member of, past their read
    cursor and not their own. "Past" is the pair (created_at, id), compared as
    the list orders it, so two messages that share a time are each counted
    once. A room never opened (no cursor) counts every message the others wrote.

    The membership conditions are in one filter() so they name one join: the
    user's own membership of the message's room, with its cursor.

    And the cursor is a range on the room's history index (room, created_at):
    "at or after the cursor's time" alone reads only what follows the cursor,
    and the rest of the pair is checked on those rows. Written as one OR --
    no cursor, or later, or the same time and a later id -- the condition
    cannot be a range, and every count would read the room's whole history.
    A room never opened starts at the earliest time there is.
    """
    cursor_at = Coalesce(F("room__memberships__read_at"), Value(EARLIEST, output_field=DateTimeField()))
    mine_and_unread = Q(room__memberships__user_id=user.pk, created_at__gte=cursor_at) & (
        Q(room__memberships__read_at__isnull=True)
        | Q(created_at__gt=F("room__memberships__read_at"))
        | Q(id__gt=F("room__memberships__read_id"))
    )
    rows = (
        Message.objects.filter(mine_and_unread)
        .exclude(author_id=user.pk)
        .order_by()
        .values("room_id")
        .annotate(unread=Count("id"))
    )
    return {row["room_id"]: row["unread"] for row in rows}


class Lv4RoomList(Component):
    """
    The room list, with each room's unread messages for the user who looks at it.

    Demonstrates:
    - a topic of one user's own (user_topic): what reaches it is that user's
      news, and no one else's page hears it
    - a notice that carries no count: told "count again", the list asks the
      database, so a notice that is late, doubled or crossed with another
      still ends in the right number
    - a count in one query for every room (unread_counts)
    - Join and Leave as events: in wireview as it is, a form posted from a
      live render would carry no CSRF token (#206)
    - what moving them from views costs, and how it is had back: a view read
      request.user on every POST, and a socket keeps the user it connected
      with. So the list's page is a boundary of its own (chat_lv4_rooms, no
      policy), and a logout closes the list's socket instead of leaving it to
      join and leave rooms as someone who signed out

    Not in the chat_lv4 live_session: the list is every room, a member's or not.
    Anyone signed in may see it; an anonymous visitor has no rooms and no topic.
    """

    class Meta:
        template_name = "chat_lv4/room_list.html"
        live_sessions = {"chat_lv4_rooms"}

    def get_subscriptions(self) -> set[str]:
        return {user_topic(self.user.pk)} if self.user.is_authenticated else set()

    @property
    def rooms(self) -> list[Room]:
        """Every room, each with ``joined`` and, for the user's own rooms, ``unread``."""
        if not self.user.is_authenticated:
            return []
        counts = unread_counts(self.user)
        rooms = list(
            Room.objects.annotate(joined=Exists(Membership.objects.filter(room=OuterRef("pk"), user_id=self.user.pk)))
        )
        for room in rooms:
            room.unread = counts.get(room.id, 0)  # type: ignore[attr-defined]
        return rooms

    async def notification(self, channel: str, **kwargs):
        """News of one of this user's rooms. Whatever it was, the render counts again."""

    async def join_room(self, room_id: UUID):
        """Become a member of a room, and go in."""
        room = await Room.objects.filter(id=room_id).afirst()
        if room is None or not self.user.is_authenticated:
            self.skip_render()
            return
        await Membership.objects.aget_or_create(room=room, user=self.user)
        # The user's other lists show the link and the badge
        await self.broadcast(user_topic(self.user.pk), room=str(room.id), event="joined")
        await self.wire.redirect_to(reverse("chat_lv4:room", args=[room.id]))

    async def leave_room(self, room_id: UUID):
        """Stop being a member. Pages of the room this user still has open go back to the list."""
        room = await Room.objects.filter(id=room_id).afirst()
        if room is None or not self.user.is_authenticated:
            self.skip_render()
            return
        await sync_to_async(end_membership)(room, self.user)
