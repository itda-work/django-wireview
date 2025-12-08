"""
Chat App Components

This module demonstrates wireview's real-time communication patterns:
- Streams API for efficient message list updates
- self.broadcast() for pending-aware cross-component notifications
- notification() hook for handling custom broadcasts
- Real-time presence tracking with sync support
- leaving() lifecycle hook for disconnect handling
"""

from wireview.component import Component
from wireview.js import JS
from wireview.schemas import ModelAction

from .models import Message, Room


class XChatRoom(Component):
    """
    Main chat room component.

    Demonstrates:
    - Nested components (XMessageList, XOnlineUsers)
    - self.broadcast() for pending-aware presence notifications
    - leaving() lifecycle hook for disconnect cleanup
    - Event modifiers (keypress.enter.prevent, input.debounce)
    - skip_render() for optimization
    """

    _template_name = "chat/room_component.html"

    # Room is a Django model - serialized by PK automatically
    room: Room
    username: str = "Anonymous"
    is_typing: bool = False

    async def joined(self):
        """
        Called when component mounts.

        Broadcast presence to notify other users in the room.
        Uses self.broadcast() which is pending-aware - the broadcast
        will be sent after all components have subscribed.
        """
        await self.broadcast(
            f"room.{self.room.id}.presence",
            action="joined",
            username=self.username,
        )

    async def leaving(self):
        """
        Called when WebSocket disconnects.

        Broadcast "left" action so other users can remove this user
        from their online users list.
        """
        await self.broadcast(
            f"room.{self.room.id}.presence",
            action="left",
            username=self.username,
        )

    async def send_message(self, content: str):
        """
        Send a new message to the room.

        Uses skip_render() because mutation() will handle the update
        when the message is created. Clears the input field via push_js().
        """
        content = content.strip()
        if content:
            await Message.objects.acreate(
                room=self.room,
                username=self.username,
                content=content,
            )
        # Clear the input field
        await self.push_js(JS().set_value("input[name=content]", ""))
        self.skip_render()

    async def set_typing(self, typing: bool = True):
        """
        Update typing indicator.

        Broadcasts typing status to other users in the room.
        """
        if self.is_typing != typing:
            self.is_typing = typing
            await self.broadcast(
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
    - stream_insert() for real-time message updates via mutation()
    - Efficient memory usage with large lists
    - Template pattern with _item.html
    """

    _template_name = "chat/message_list.html"
    _subscriptions = {"message"}

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
    - joined() for self-registration
    - Presence sync via request-response pattern
    """

    _template_name = "chat/online_users.html"

    room: Room
    username: str = ""  # Current user's username for self-registration
    online_users: dict[str, bool] = {}  # username -> is_typing

    async def joined(self):
        """
        Register self in online_users when joining.

        This ensures the current user appears in the online users list
        immediately upon joining, without waiting for a broadcast.

        Also requests presence sync from other users to get the current
        list of online users.
        """
        if self.username:
            self.online_users[self.username] = False
            # Request presence sync from existing users
            await self.broadcast(
                f"room.{self.room.id}.presence",
                action="sync_request",
                requester=self.username,
            )

    @property
    def _subscriptions(self):
        """Subscribe to room-specific presence channel."""
        return {f"room.{self.room.id}.presence"}

    async def notification(self, channel: str, **kwargs):
        """
        Handle presence notifications from other components.

        Called when any component broadcasts to the subscribed channel.
        Supports: joined, left, typing, sync_request, sync_response
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

        elif action == "sync_request":
            # Another user is requesting presence info
            requester = kwargs.get("requester")
            if requester != self.username and self.username:
                # Respond with our presence
                await self.broadcast(
                    f"room.{self.room.id}.presence",
                    action="sync_response",
                    username=self.username,
                    is_typing=self.online_users.get(self.username, False),
                )

        elif action == "sync_response":
            # Received presence info from another user
            if username and username != self.username:
                is_typing = kwargs.get("is_typing", False)
                self.online_users[username] = is_typing
                self.force_render()
