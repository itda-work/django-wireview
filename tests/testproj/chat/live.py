"""
Chat App Components

This module demonstrates wireview's real-time communication patterns:
- Streams API for efficient message list updates
- PresenceMixin for typing indicators with auto-timeout
- PresenceTrackerMixin for tracking online users
- Real-time presence tracking with sync support
- leaving() lifecycle hook for disconnect handling
"""

from wireview.component import Component
from wireview.features.presence import (
    PresenceConfig,
    PresenceMixin,
    PresenceTrackerMixin,
)
from wireview.js import JS
from wireview.schemas import ModelAction

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

    _template_name = "chat/room_component.html"

    # Presence configuration: 3 second typing timeout
    _presence_config = PresenceConfig(typing_timeout=3.0)

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

        Uses skip_render() because mutation() will handle the update
        when the message is created. Clears the input field via push_js().
        Also clears typing indicator when message is sent.
        """
        content = content.strip()
        if content:
            await Message.objects.acreate(
                room=self.room,
                username=self.username,
                content=content,
            )
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


class XMessageList(Component):
    """
    Message list using Streams for efficient updates.

    Demonstrates:
    - stream() for initial list population
    - stream_insert() for real-time message updates via mutation()
    - Efficient memory usage with large lists
    - Template pattern with _item.html
    """

    _template_name = "chat/message_list.html"
    _subscriptions = {"chat.message"}

    room: Room
    messages: list[Message] = []

    async def mutation(
        self,
        channel: str,
        action: ModelAction,
        instance: Message,
    ):
        """
        Handle message model changes.

        Only processes messages for this room.
        Uses stream_insert() to append new messages efficiently.
        """
        if action == ModelAction.CREATED and instance.room_id == self.room.id:
            await self.stream_insert("messages", instance, at=-1)
            # Scroll to the new message
            await self.scroll_into_view(
                f"messages-{instance.pk}",
                behavior="smooth",
                block="end",
            )

    async def joined(self):
        """
        Load initial messages using Streams.

        stream() efficiently renders each item individually and
        sends them to the client without keeping all HTML in memory.
        """
        qs = Message.objects.filter(room=self.room).order_by("-created_at")[:50]
        messages = [m async for m in qs]
        # Reverse to show oldest first
        await self.stream("messages", list(reversed(messages)))

        # Scroll to the last (newest) message
        if messages:
            last_msg = messages[0]  # First in -created_at order = newest
            await self.scroll_into_view(
                f"messages-{last_msg.pk}",
                behavior="instant",
                block="end",
            )


class XOnlineUsers(PresenceTrackerMixin, Component):
    """
    Display online users with typing indicators.

    Demonstrates:
    - PresenceTrackerMixin for tracking online users
    - Dynamic @property _subscriptions
    - Automatic notification() handling from mixin
    - Presence sync via request-response pattern
    """

    _template_name = "chat/online_users.html"

    room: Room
    username: str = ""  # Current user's username for self-registration

    def _presence_topic(self) -> str:
        """Return room-specific topic for presence channel."""
        return f"room.{self.room.id}"

    def _presence_my_user_id(self) -> str:
        """Use username as user ID."""
        return self.username

    @property
    def _subscriptions(self):
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
