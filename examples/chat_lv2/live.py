"""
Chat App Components (level 2)

The chat example, where the server decides who is speaking:
- The author of a message, and the name in the online list, come from
  ``self.user`` -- the connection's signed-in user. No handler takes a name and
  no state field holds one.
- Every component here lives inside the ``chat_lv2`` live_session
  (``Meta.live_sessions``), whose hook refuses a user who is not a member of
  the component's room, on every path that mounts it (``access.py``).
- Leaving a room ends the membership's open pages: they hear it on a topic of
  their own and go back to the room list.

Everything else is the chat example: Streams, Broadcast, Presence.
"""

from asgiref.sync import sync_to_async
from django.urls import reverse

from wireview import (
    JS,
    Broadcast,
    Component,
    PresenceConfig,
    PresenceMixin,
    PresenceTrackerMixin,
    abroadcast,
)

from .access import is_member
from .models import Membership, Message, Room


def room_topic(room: Room) -> str:
    """Where a room's new messages are broadcast. Only that room's message lists hear it."""
    return f"chat_lv2.room.{room.id}"


def membership_topic(room_id, user_id) -> str:
    """Told when one user's membership of one room ends. Their pages of that room hear it."""
    return f"chat_lv2.membership.{room_id}.{user_id}"


async def aend_membership(room: Room, user) -> None:
    """Take ``user`` out of ``room`` and send their open pages of it back to the room list."""
    await Membership.objects.filter(room=room, user_id=user.pk).adelete()
    await abroadcast(membership_topic(room.id, user.pk))


class Lv2ChatRoom(PresenceMixin, Component):
    """
    Main chat room component.

    Demonstrates:
    - The author and the presence name read from self.user, never from the browser
    - A membership check in the handler that writes: events are not authorized again
    - Nested components (Lv2MessageList, Lv2OnlineUsers)
    - PresenceMixin for typing indicators with auto-timeout
    - leaving() lifecycle hook for disconnect handling
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

    def get_subscriptions(self) -> set[str]:
        return {membership_topic(self.room.id, self.user.pk)}

    def _presence_topic(self) -> str:
        """Return room-specific topic for presence channel."""
        return f"chat_lv2.room.{self.room.id}"

    def _presence_user_id(self) -> str:
        """The user's primary key: two people may not share it, whatever they are called."""
        return str(self.user.pk)

    def _presence_username(self) -> str:
        return self.user.get_username()

    async def joined(self):
        await self.presence_join()

    async def leaving(self):
        await self.presence_leave()

    async def notification(self, channel: str, **kwargs):
        """This user's membership of the room ended, on this page or another."""
        if channel == membership_topic(self.room.id, self.user.pk):
            await self.wire.redirect_to(reverse("chat_lv2:index"))
            return
        await super().notification(channel, **kwargs)

    async def send_message(self, content: str):
        """
        Send a new message to the room, written by this connection's user.

        The page was let in when the user was a member; that may have ended
        since, so the handler asks again before it writes.
        """
        content = content.strip()
        if content and await self._still_member():
            message = await Message.objects.acreate(room=self.room, author=self.user, content=content)
            await Broadcast(Lv2MessageList, room_topic(self.room)).stream_insert("messages", message, at=0).asend()
        # Clear typing indicator when sending message
        await self.presence_set_typing(False)
        # Clear the input field
        await self.push_js(JS().set_value("input[name=content]", ""))
        self.skip_render()

    async def on_typing(self):
        await self.presence_set_typing(True)

    async def leave_room(self):
        """Stop being a member. This user's pages of the room, this one included, go to the room list."""
        await aend_membership(self.room, self.user)
        await self.wire.redirect_to(reverse("chat_lv2:index"))

    async def _still_member(self) -> bool:
        return await sync_to_async(is_member)(self.user, self.room)


class Lv2MessageList(Component):
    """
    Message list using Streams for efficient updates.

    Demonstrates:
    - stream() for initial list population
    - a Broadcast from Lv2ChatRoom.send_message for each new message
    - get_subscriptions() narrowing the topic to the room

    The list is newest first in the DOM and drawn bottom up
    (flex-direction: column-reverse), so a new message goes in at=0 and the
    list stays scrolled to the newest one without a scroll command per page.
    """

    class Meta:
        template_name = "chat_lv2/message_list.html"
        live_sessions = {"chat_lv2"}

    room: Room
    messages: list[Message] = []

    def get_subscriptions(self) -> set[str]:
        return {room_topic(self.room)}

    async def joined(self):
        latest = Message.objects.filter(room=self.room).select_related("author").order_by("-created_at")[:50]
        await self.stream("messages", latest)


class Lv2OnlineUsers(PresenceTrackerMixin, Component):
    """
    Display online users with typing indicators.

    Demonstrates:
    - PresenceTrackerMixin for tracking online users
    - The user registers itself under its own primary key and name
    """

    class Meta:
        template_name = "chat_lv2/online_users.html"
        live_sessions = {"chat_lv2"}

    room: Room

    def _presence_topic(self) -> str:
        return f"chat_lv2.room.{self.room.id}"

    def _presence_my_user_id(self) -> str:
        return str(self.user.pk)

    def get_subscriptions(self) -> set[str]:
        return {self._presence_channel()}

    async def joined(self):
        await self.presence_track_self(username=self.user.get_username())
