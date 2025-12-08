"""Presence module for real-time user presence and typing indicators.

This module provides Phoenix LiveView-style presence tracking for wireview
components, enabling:
- Typing indicators with auto-timeout
- Online/offline user tracking
- Presence synchronization between users

Example:
    from wireview.component import Component
    from wireview.features.presence import PresenceMixin, PresenceTrackerMixin

    class ChatInput(PresenceMixin, Component):
        room_id: int
        username: str

        def _presence_topic(self) -> str:
            return f"room.{self.room_id}"

        def _presence_user_id(self) -> str:
            return self.username

        def _presence_username(self) -> str:
            return self.username

        async def joined(self):
            await self.presence_join()

        async def leaving(self):
            await self.presence_leave()

        async def on_typing(self):
            await self.presence_set_typing(True)

    class OnlineUsers(PresenceTrackerMixin, Component):
        room_id: int
        username: str

        def _presence_topic(self) -> str:
            return f"room.{self.room_id}"

        def _presence_my_user_id(self) -> str:
            return self.username

        @property
        def _subscriptions(self):
            return {self._presence_channel()}

        async def joined(self):
            await self.presence_track_self(username=self.username)
"""

from __future__ import annotations

import asyncio
import time
import typing as t
from dataclasses import dataclass, field
from enum import Enum

if t.TYPE_CHECKING:
    from wireview.core.component import Component


__all__ = (
    "PresenceState",
    "PresenceUser",
    "PresenceConfig",
    "PresenceMixin",
    "PresenceTrackerMixin",
)


class PresenceState(str, Enum):
    """Possible states for a user's presence."""

    ONLINE = "online"
    TYPING = "typing"
    OFFLINE = "offline"


@dataclass
class PresenceUser:
    """Represents a user's presence state.

    Attributes:
        user_id: Unique identifier for the user
        username: Display name
        state: Current presence state
        last_active: Timestamp of last activity
        metadata: Optional custom data (avatar_url, etc.)
    """

    user_id: str
    username: str
    state: PresenceState = PresenceState.ONLINE
    last_active: float = field(default_factory=time.time)
    metadata: dict[str, t.Any] = field(default_factory=dict)

    def is_typing(self) -> bool:
        """Check if user is currently typing."""
        return self.state == PresenceState.TYPING

    def is_online(self) -> bool:
        """Check if user is online (any non-offline state)."""
        return self.state != PresenceState.OFFLINE


@dataclass
class PresenceConfig:
    """Configuration for presence tracking.

    Attributes:
        typing_timeout: Seconds until typing auto-clears (default 3.0)
        sync_on_join: Request sync from other users on join
        channel_prefix: Prefix for presence channels
    """

    typing_timeout: float = 3.0
    sync_on_join: bool = True
    channel_prefix: str = "presence"


class PresenceMixin:
    """Mixin for components that emit presence updates.

    Provides typing indicator support with automatic server-side timeout.
    This mixin is for "producer" components that broadcast their own
    presence state to others.

    Required methods to implement:
        _presence_topic(): Return the topic/room identifier
        _presence_user_id(): Return unique user identifier
        _presence_username(): Return display name

    Optional methods:
        _presence_metadata(): Return custom metadata dict

    Usage in lifecycle hooks:
        async def joined(self):
            await self.presence_join()

        async def leaving(self):
            await self.presence_leave()

    Usage in event handlers:
        async def on_typing(self):
            await self.presence_set_typing(True)
    """

    # Configuration (override in subclass if needed)
    _presence_config: t.ClassVar[PresenceConfig] = PresenceConfig()

    # Internal state stored in __dict__ to avoid Pydantic serialization
    @property
    def _presence_typing_task(self) -> asyncio.Task[None] | None:
        return self.__dict__.get("__presence_typing_task")

    @_presence_typing_task.setter
    def _presence_typing_task(self, value: asyncio.Task[None] | None) -> None:
        self.__dict__["__presence_typing_task"] = value

    @property
    def _presence_is_typing(self) -> bool:
        return self.__dict__.get("__presence_is_typing", False)

    @_presence_is_typing.setter
    def _presence_is_typing(self, value: bool) -> None:
        self.__dict__["__presence_is_typing"] = value

    def _presence_topic(self) -> str:
        """Return the presence topic/room identifier.

        Example: f"room.{self.room_id}" or f"document.{self.doc_id}"
        """
        raise NotImplementedError("Subclass must implement _presence_topic()")

    def _presence_user_id(self) -> str:
        """Return unique identifier for current user."""
        raise NotImplementedError("Subclass must implement _presence_user_id()")

    def _presence_username(self) -> str:
        """Return display name for current user."""
        raise NotImplementedError("Subclass must implement _presence_username()")

    def _presence_metadata(self) -> dict[str, t.Any]:
        """Return optional metadata for current user."""
        return {}

    def _presence_channel(self) -> str:
        """Get the broadcast channel for presence updates."""
        prefix = self._presence_config.channel_prefix
        return f"{prefix}.{self._presence_topic()}"

    def _presence_user_data(self, state: PresenceState = PresenceState.ONLINE) -> dict[str, t.Any]:
        """Build user data for broadcast."""
        return {
            "user_id": self._presence_user_id(),
            "username": self._presence_username(),
            "state": state.value,
            "metadata": self._presence_metadata(),
        }

    async def presence_join(self) -> None:
        """Announce presence join. Call in joined() lifecycle hook.

        Broadcasts "presence_join" action to presence channel.
        """
        # Type assertion for Component methods
        component = t.cast("Component", self)

        await component.broadcast(
            self._presence_channel(),
            action="presence_join",
            **self._presence_user_data(PresenceState.ONLINE),
        )

    async def presence_leave(self) -> None:
        """Announce presence leave. Call in leaving() lifecycle hook.

        Broadcasts "presence_leave" and cancels any pending typing timeout.
        """
        # Cancel typing timeout if active
        if self._presence_typing_task and not self._presence_typing_task.done():
            self._presence_typing_task.cancel()
            try:
                await self._presence_typing_task
            except asyncio.CancelledError:
                pass
            self._presence_typing_task = None

        component = t.cast("Component", self)
        await component.broadcast(
            self._presence_channel(),
            action="presence_leave",
            user_id=self._presence_user_id(),
            username=self._presence_username(),
        )

    async def presence_set_typing(self, typing: bool = True) -> None:
        """Set typing indicator with auto-timeout.

        When typing=True:
        - Broadcasts typing state
        - Starts auto-timeout timer that clears typing after N seconds
        - Calling again resets the timer

        When typing=False:
        - Clears typing state immediately
        - Cancels pending timeout

        Args:
            typing: Whether user is currently typing
        """
        # Cancel existing timeout task
        if self._presence_typing_task and not self._presence_typing_task.done():
            self._presence_typing_task.cancel()
            try:
                await self._presence_typing_task
            except asyncio.CancelledError:
                pass
            self._presence_typing_task = None

        component = t.cast("Component", self)

        # Only broadcast if state actually changed
        if self._presence_is_typing != typing:
            self._presence_is_typing = typing
            state = PresenceState.TYPING if typing else PresenceState.ONLINE

            await component.broadcast(
                self._presence_channel(),
                action="presence_typing",
                **self._presence_user_data(state),
            )

        # Start auto-timeout if typing
        if typing:
            self._presence_typing_task = asyncio.create_task(self._presence_typing_timeout())

        # Optimize: don't re-render just for typing state change
        component.skip_render()

    async def _presence_typing_timeout(self) -> None:
        """Auto-clear typing after timeout."""
        try:
            await asyncio.sleep(self._presence_config.typing_timeout)
            # Timeout expired, clear typing
            if self._presence_is_typing:
                self._presence_is_typing = False
                component = t.cast("Component", self)
                await component.broadcast(
                    self._presence_channel(),
                    action="presence_typing",
                    **self._presence_user_data(PresenceState.ONLINE),
                )
        except asyncio.CancelledError:
            # Task was cancelled (user typed again or left)
            pass


class PresenceTrackerMixin:
    """Mixin for components that track presence of other users.

    Maintains a registry of online users and their states. This mixin
    is for "consumer" components that display other users' presence.

    Required methods to implement:
        _presence_topic(): Return the topic/room identifier
        _presence_my_user_id(): Return current user's ID

    Usage:
        @property
        def _subscriptions(self):
            return {self._presence_channel()}

        async def joined(self):
            await self.presence_track_self(username=self.username)

    Template:
        {% for user in this.presence_users %}
            {{ user.username }}
            {% if user.is_typing %}<span>typing...</span>{% endif %}
        {% endfor %}
    """

    # Configuration
    _presence_config: t.ClassVar[PresenceConfig] = PresenceConfig()

    # Internal state stored in __dict__ to avoid Pydantic serialization
    @property
    def _presence_registry(self) -> dict[str, PresenceUser]:
        if "__presence_registry" not in self.__dict__:
            self.__dict__["__presence_registry"] = {}
        return self.__dict__["__presence_registry"]

    @_presence_registry.setter
    def _presence_registry(self, value: dict[str, PresenceUser]) -> None:
        self.__dict__["__presence_registry"] = value

    def _presence_topic(self) -> str:
        """Return the presence topic/room identifier."""
        raise NotImplementedError("Subclass must implement _presence_topic()")

    def _presence_my_user_id(self) -> str:
        """Return current user's ID (to identify self)."""
        raise NotImplementedError("Subclass must implement _presence_my_user_id()")

    def _presence_channel(self) -> str:
        """Get the broadcast channel for presence updates."""
        prefix = self._presence_config.channel_prefix
        return f"{prefix}.{self._presence_topic()}"

    @property
    def presence_users(self) -> list[PresenceUser]:
        """Get list of all tracked users (for template iteration)."""
        return list(self._presence_registry.values())

    @property
    def presence_online_count(self) -> int:
        """Get count of online users."""
        return sum(1 for u in self._presence_registry.values() if u.is_online())

    @property
    def presence_typing_users(self) -> list[PresenceUser]:
        """Get list of users currently typing."""
        return [u for u in self._presence_registry.values() if u.is_typing()]

    def presence_get_user(self, user_id: str) -> PresenceUser | None:
        """Get a specific user by ID."""
        return self._presence_registry.get(user_id)

    async def presence_track_self(
        self,
        username: str,
        metadata: dict[str, t.Any] | None = None,
    ) -> None:
        """Register self in the presence list and request sync.

        Call this in joined() to add current user to the presence list
        and request presence info from other users.
        """
        user_id = self._presence_my_user_id()
        self._presence_registry[user_id] = PresenceUser(
            user_id=user_id,
            username=username,
            state=PresenceState.ONLINE,
            metadata=metadata or {},
        )

        # Request sync from other users
        if self._presence_config.sync_on_join:
            component = t.cast("Component", self)
            await component.broadcast(
                self._presence_channel(),
                action="presence_sync_request",
                requester_id=user_id,
            )

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        """Handle presence notifications.

        Processes: presence_join, presence_leave, presence_typing,
        presence_sync_request, presence_sync_response
        """
        action = kwargs.get("action", "")

        if not action.startswith("presence_"):
            # Not a presence notification, call parent if exists
            parent_notification = getattr(super(), "notification", None)
            if parent_notification is not None:
                await parent_notification(channel, **kwargs)
            return

        component = t.cast("Component", self)
        user_id: str | None = kwargs.get("user_id")

        if action == "presence_join" and user_id:
            self._presence_registry[user_id] = PresenceUser(
                user_id=user_id,
                username=kwargs.get("username", ""),
                state=PresenceState(kwargs.get("state", "online")),
                metadata=kwargs.get("metadata", {}),
            )
            component.force_render()

        elif action == "presence_leave" and user_id:
            self._presence_registry.pop(user_id, None)
            component.force_render()

        elif action == "presence_typing" and user_id:
            if user_id in self._presence_registry:
                self._presence_registry[user_id].state = PresenceState(kwargs.get("state", "online"))
                self._presence_registry[user_id].last_active = time.time()
            else:
                # User not tracked yet, add them
                self._presence_registry[user_id] = PresenceUser(
                    user_id=user_id,
                    username=kwargs.get("username", ""),
                    state=PresenceState(kwargs.get("state", "online")),
                    metadata=kwargs.get("metadata", {}),
                )
            component.force_render()

        elif action == "presence_sync_request":
            # Another user is requesting presence info
            requester_id = kwargs.get("requester_id")
            my_id = self._presence_my_user_id()

            if requester_id != my_id and my_id in self._presence_registry:
                # Respond with our presence
                my_presence = self._presence_registry[my_id]
                await component.broadcast(
                    channel,
                    action="presence_sync_response",
                    user_id=my_presence.user_id,
                    username=my_presence.username,
                    state=my_presence.state.value,
                    metadata=my_presence.metadata,
                )

        elif action == "presence_sync_response":
            # Received presence info from another user
            if user_id and user_id != self._presence_my_user_id():
                self._presence_registry[user_id] = PresenceUser(
                    user_id=user_id,
                    username=kwargs.get("username", ""),
                    state=PresenceState(kwargs.get("state", "online")),
                    metadata=kwargs.get("metadata", {}),
                )
                component.force_render()
