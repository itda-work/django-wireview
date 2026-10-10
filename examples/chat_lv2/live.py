"""
Chat App Components (level 2)

The chat example, where the server decides who is speaking:
- The author of a message, and the name in the online list, come from
  ``self.user`` -- the connection's signed-in user. No handler takes a name and
  no state field holds one.
- Every component here lives inside the ``chat_lv2`` live_session
  (``Meta.live_sessions``), whose hook refuses a user who is not a member of
  the component's room, on every path that mounts it (``access.py``).
- Leaving a room ends what the membership's open pages hear of it: told on a
  topic of their own, each component asks again and gives up the room's
  topics on the server, and the page is sent back to the room list.

The rest is the chat example's:
- Streams API for efficient message list updates
- Broadcast: a new message rendered once and put on every open page of the room
- PresenceMixin for typing indicators with auto-timeout
- PresenceTrackerMixin for tracking online users
- Real-time presence tracking with sync support
- leaving() lifecycle hook for disconnect handling
"""

from asgiref.sync import sync_to_async
from django.db import transaction
from django.urls import reverse

from wireview import (
    JS,
    Broadcast,
    Component,
    PresenceConfig,
    PresenceMixin,
    PresenceTrackerMixin,
    broadcast,
)

from .access import ais_member
from .models import Membership, Message, Room


def membership_topic(room_id, user_id) -> str:
    """Told when one user's membership of one room ends. Their components of that room hear it."""
    return f"chat_lv2.membership.{room_id}.{user_id}"


def end_membership(room: Room, user) -> None:
    """
    Take ``user`` out of ``room``, and tell their open pages of it.

    The notice goes out once the delete is committed. A component that hears
    it asks ``is_member`` again, and must get the answer after the delete: in
    a transaction (the view's ATOMIC_REQUESTS) it waits for the commit, and
    outside one on_commit runs it at once.
    """
    Membership.objects.filter(room=room, user_id=user.pk).delete()
    transaction.on_commit(lambda: broadcast(membership_topic(room.id, user.pk)))


# The components of the room page share one rule after they mount. The boundary's
# hook let them in and is not asked again while the page stays open, and a redirect
# only asks the browser to go. So each one also listens on this user's membership
# topic, and when told asks is_member again: the notice says "ask", the database
# says what is true. If the answer is no it sets _left, and get_subscriptions()
# leaves out every topic of the room. The session reads get_subscriptions() of each
# component again after it delivered a notification, and unsubscribes the
# connection from the topics no component asks for any more
# (WireviewSession._dispatch_notifications -> after_mutation_chores). From then on
# the socket gets none of the room's messages or presence, whether the page went or
# not.


class Lv2ChatRoom(PresenceMixin, Component):
    """
    Main chat room component.

    Demonstrates:
    - The author and the presence name read from self.user, never from the browser
    - A membership check in the handlers: events are not authorized again
    - Nested components (Lv2MessageList, Lv2OnlineUsers)
    - PresenceMixin for typing indicators with auto-timeout
    - leaving() lifecycle hook for disconnect cleanup
    - Event modifiers (keypress.enter.prevent, input.debounce)
    - skip_render() for optimization
    """

    class Meta:
        template_name = "chat_lv2/room_component.html"
        live_sessions = {"chat_lv2"}
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
        return f"chat_lv2.room.{self.room.id}"

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
        """
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
        """
        content = content.strip()
        if content and await ais_member(self.user, self.room):
            message = await Message.objects.acreate(room=self.room, author=self.user, content=content)
            await Broadcast(Lv2MessageList, room_topic(self.room)).stream_insert("messages", message, at=0).asend()
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
        await self.wire.redirect_to(reverse("chat_lv2:index"))


def room_topic(room: Room) -> str:
    """Where a room's new messages are broadcast. Only that room's message lists hear it."""
    return f"chat_lv2.room.{room.id}"


class Lv2MessageList(Component):
    """
    Message list using Streams for efficient updates.

    Demonstrates:
    - stream() for initial list population
    - a Broadcast from Lv2ChatRoom.send_message for each new message: no code
      of this component runs for it
    - get_subscriptions() narrowing the topic to the room
    - Efficient memory usage with large lists
    - Template pattern with _item.html

    The list is newest first in the DOM and drawn bottom up
    (flex-direction: column-reverse), so a new message goes in at=0 and the
    list stays scrolled to the newest one without a scroll command per page.
    """

    class Meta:
        template_name = "chat_lv2/message_list.html"
        live_sessions = {"chat_lv2"}

    room: Room
    messages: list[Message] = []
    # Set when this user's membership of the room ends while the page is open
    _left: bool = False

    def get_subscriptions(self) -> set[str]:
        if self._left:
            return set()
        return {room_topic(self.room), membership_topic(self.room.id, self.user.pk)}

    async def notification(self, channel: str, **kwargs):
        """This user's membership of the room may have ended: ask, and stop hearing the room if it did."""
        if channel == membership_topic(self.room.id, self.user.pk):
            self._left = not await ais_member(self.user, self.room)
            self.skip_render()

    async def joined(self):
        """
        Load initial messages using Streams.

        stream() efficiently renders each item individually and
        sends them to the client without keeping all HTML in memory.
        """
        latest = Message.objects.filter(room=self.room).select_related("author").order_by("-created_at")[:50]
        await self.stream("messages", latest)


class Lv2OnlineUsers(PresenceTrackerMixin, Component):
    """
    Display online users with typing indicators.

    Demonstrates:
    - PresenceTrackerMixin for tracking online users
    - Dynamic @property _subscriptions
    - Automatic notification() handling from mixin
    - Presence sync via request-response pattern
    """

    class Meta:
        template_name = "chat_lv2/online_users.html"
        live_sessions = {"chat_lv2"}

    room: Room
    # Set when this user's membership of the room ends while the page is open
    _left: bool = False

    def _presence_topic(self) -> str:
        """Return room-specific topic for presence channel."""
        return f"chat_lv2.room.{self.room.id}"

    def _presence_my_user_id(self) -> str:
        """The user's primary key, as Lv2ChatRoom announces it."""
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
