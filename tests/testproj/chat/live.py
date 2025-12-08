"""
Chat App Components

This module demonstrates wireview's real-time communication patterns:
- Streams API for efficient message list updates
- abroadcast() for cross-component notifications
- notification() hook for handling custom broadcasts
- Real-time presence tracking
"""

from wireview import abroadcast
from wireview.component import Component
from wireview.schemas import ModelAction

from .models import Message, Room


class XChatRoom(Component):
    """
    Main chat room component.

    Demonstrates:
    - Streams API for message list (stream_insert)
    - broadcast() for presence notifications
    - Event modifiers (keypress.enter.prevent, input.debounce)
    - skip_render() for optimization
    """

    _template_name = "chat/room_component.html"
    _subscriptions = {"message"}

    # Room is a Django model - serialized by PK automatically
    room: Room
    username: str = "Anonymous"
    is_typing: bool = False

    async def joined(self):
        """
        Called when component mounts.

        Broadcast presence to notify other users in the room.
        Uses abroadcast() for async context.
        """
        await abroadcast(
            f"room.{self.room.id}.presence",
            action="joined",
            username=self.username,
        )

    async def send_message(self, content: str):
        """
        Send a new message to the room.

        Uses skip_render() because mutation() will handle the update
        when the message is created.
        """
        content = content.strip()
        if content:
            await Message.objects.acreate(
                room=self.room,
                username=self.username,
                content=content,
            )
        self.skip_render()

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
            # Append message to the stream (at=-1 means append)
            await self.stream_insert("messages", instance, at=-1)

    async def set_typing(self, typing: bool = True):
        """
        Update typing indicator.

        Broadcasts typing status to other users in the room.
        """
        if self.is_typing != typing:
            self.is_typing = typing
            await abroadcast(
                f"room.{self.room.id}.presence",
                action="typing",
                username=self.username,
                is_typing=typing,
            )
        self.skip_render()


class XMessageList(Component):
    """
    Message list using Streams for efficient updates.

    Demonstrates:
    - stream() for initial list population
    - Efficient memory usage with large lists
    - Template pattern with _item.html
    """

    _template_name = "chat/message_list.html"

    room: Room
    messages: list[Message] = []

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


class XOnlineUsers(Component):
    """
    Display online users with typing indicators.

    Demonstrates:
    - notification() hook for custom broadcasts
    - Dynamic @property _subscriptions
    - force_render() to update on notifications
    """

    _template_name = "chat/online_users.html"

    room: Room
    online_users: dict[str, bool] = {}  # username -> is_typing

    @property
    def _subscriptions(self):
        """Subscribe to room-specific presence channel."""
        return {f"room.{self.room.id}.presence"}

    async def notification(self, channel: str, **kwargs):
        """
        Handle presence notifications from other components.

        Called when any component broadcasts to the subscribed channel.
        """
        action = kwargs.get("action")
        username = kwargs.get("username")

        if action == "joined":
            self.online_users[username] = False
            self.force_render()
        elif action == "typing":
            is_typing = kwargs.get("is_typing", False)
            self.online_users[username] = is_typing
            self.force_render()
        elif action == "left":
            self.online_users.pop(username, None)
            self.force_render()
