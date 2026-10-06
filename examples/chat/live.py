"""
Chat App Components

This module demonstrates wireview's real-time communication patterns:
- Streams API for efficient message list updates
- Broadcast: a new message rendered once and put on every open page of the room
- PresenceMixin for typing indicators with auto-timeout
- PresenceTrackerMixin for tracking online users
- Real-time presence tracking with sync support
- leaving() lifecycle hook for disconnect handling
"""

from wireview import (
    JS,
    Broadcast,
    Component,
    PresenceConfig,
    PresenceMixin,
    PresenceTrackerMixin,
)

from .models import Message, Room


class XChatRoom(PresenceMixin, Component):
    """
    Main chat room component.

    Demonstrates:
    - Nested components (XMessageList, XOnlineUsers)
    - PresenceMixin for typing indicators with auto-timeout
    - leaving() lifecycle hook for disconnect cleanup
    - Event modifiers (keypress.enter.prevent, input.debounce)
    - skip_render() for optimization
    """

    class Meta:
        template_name = "chat/room_component.html"
        # Presence configuration: 3 second typing timeout
        presence = PresenceConfig(typing_timeout=3.0)

    # Room is a Django model - serialized by PK automatically
    room: Room
    username: str = "Anonymous"

    def _presence_topic(self) -> str:
        """Return room-specific topic for presence channel."""
        return f"room.{self.room.id}"

    def _presence_user_id(self) -> str:
        """Use username as user ID."""
        return self.username

    def _presence_username(self) -> str:
        """Return display username."""
        return self.username

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

    async def send_message(self, content: str):
        """
        Send a new message to the room.

        The message is rendered once, here, and every page with this room's
        message list open -- this one too -- puts it in its list: a Broadcast
        runs no code on the pages it reaches. Clears the input field via
        push_js() and the typing indicator; nothing of this component changes,
        so skip_render().
        """
        content = content.strip()
        if content:
            message = await Message.objects.acreate(
                room=self.room,
                username=self.username,
                content=content,
            )
            await Broadcast(XMessageList, room_topic(self.room)).stream_insert("messages", message, at=0).asend()
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
        """
        await self.presence_set_typing(True)


def room_topic(room: Room) -> str:
    """Where a room's new messages are broadcast. Only that room's message lists hear it."""
    return f"chat.room.{room.id}"


class XMessageList(Component):
    """
    Message list using Streams for efficient updates.

    Demonstrates:
    - stream() for initial list population
    - a Broadcast from XChatRoom.send_message for each new message: no code
      of this component runs for it
    - get_subscriptions() narrowing the topic to the room
    - Efficient memory usage with large lists
    - Template pattern with _item.html

    The list is newest first in the DOM and drawn bottom up
    (flex-direction: column-reverse), so a new message goes in at=0 and the
    list stays scrolled to the newest one without a scroll command per page.
    """

    class Meta:
        template_name = "chat/message_list.html"

    room: Room
    messages: list[Message] = []

    def get_subscriptions(self) -> set[str]:
        return {room_topic(self.room)}

    async def joined(self):
        """
        Load initial messages using Streams.

        stream() efficiently renders each item individually and
        sends them to the client without keeping all HTML in memory.
        """
        await self.stream("messages", Message.objects.filter(room=self.room).order_by("-created_at")[:50])


class XOnlineUsers(PresenceTrackerMixin, Component):
    """
    Display online users with typing indicators.

    Demonstrates:
    - PresenceTrackerMixin for tracking online users
    - Dynamic @property _subscriptions
    - Automatic notification() handling from mixin
    - Presence sync via request-response pattern
    """

    class Meta:
        template_name = "chat/online_users.html"

    room: Room
    username: str = ""  # Current user's username for self-registration

    def _presence_topic(self) -> str:
        """Return room-specific topic for presence channel."""
        return f"room.{self.room.id}"

    def _presence_my_user_id(self) -> str:
        """Use username as user ID."""
        return self.username

    def get_subscriptions(self) -> set[str]:
        """Subscribe to room-specific presence channel."""
        return {self._presence_channel()}

    async def joined(self):
        """
        Register self in presence list when joining.

        Uses PresenceTrackerMixin.presence_track_self() which:
        - Adds current user to the presence list
        - Requests sync from other users
        """
        if self.username:
            await self.presence_track_self(username=self.username)
