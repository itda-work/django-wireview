"""Presence module for real-time user presence and typing indicators.

This module provides Phoenix LiveView-style presence tracking for wireview
components, enabling:
- Typing indicators with auto-timeout
- Online/offline user tracking
- Presence synchronization between users

Example:
    from wireview.core.component import Component
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

        def get_subscriptions(self) -> set[str]:
            return super().get_subscriptions() | {self._presence_channel()}

        async def joined(self):
            await self.presence_track_self(username=self.username)
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
import typing as t
from dataclasses import dataclass, field
from enum import StrEnum

if t.TYPE_CHECKING:
    from wireview.core.component import Component


__all__ = (
    "PresenceState",
    "PresenceUser",
    "PresenceConfig",
    "PresenceMixin",
    "PresenceTrackerMixin",
)


# The key a meta sent without ``presence_ref`` is kept under: a process older than
# #202 tracks one meta per user, so its messages stand for one connection per user.
LEGACY_REF = ""


def _presence_ref(component: t.Any) -> str:
    """The key of this component's connection among a user's metas (#202).

    Every component of one WebSocket connection gives the same ref, so the
    connection's producer and tracker speak for one meta. It is a digest of
    ``connection_id``, not the id: that scopes the connection's uploads, and the
    ref travels to every subscriber of the topic. A component with no connection
    (``mount()`` in tests) stands for a connection of its own.
    """
    connection_id = component.wire.connection_id
    if connection_id:
        return hashlib.blake2b(connection_id.encode(), digest_size=8, person=b"wv-presence").hexdigest()
    ref = component.__dict__.get("__presence_ref")
    if ref is None:
        ref = component.__dict__["__presence_ref"] = secrets.token_hex(8)
    return ref


class PresenceState(StrEnum):
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

    # Configuration: ``presence = PresenceConfig(...)`` in the component's Meta
    @property
    def _presence_config(self) -> PresenceConfig:
        return self._meta.presence or PresenceConfig()  # type: ignore[attr-defined]

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
            "presence_ref": _presence_ref(self),
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
            presence_ref=_presence_ref(self),
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
            from wireview.core.component import _log_failure

            self._presence_typing_task = asyncio.create_task(self._presence_typing_timeout())
            # Its broadcast can fail; left alone that surfaced only when the task
            # was replaced or collected (#151)
            self._presence_typing_task.add_done_callback(
                _log_failure("%s (%s) could not clear typing", component._name, component.id)
            )

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

    Keeps a meta per connection under each user, as Phoenix Presence does (#202):
    a user with two tabs open has two metas, and leaves the list only when the
    last of them leaves. The list the template reads has one entry per user,
    merged from that user's metas (see ``_presence_merge``). This mixin is for
    "consumer" components that display other users' presence.

    Required methods to implement:
        _presence_topic(): Return the topic/room identifier
        _presence_my_user_id(): Return current user's ID

    Usage:
        def get_subscriptions(self) -> set[str]:
            return super().get_subscriptions() | {self._presence_channel()}

        async def joined(self):
            await self.presence_track_self(username=self.username)

    Template:
        {% for user in this.presence_users %}
            {{ user.username }}
            {% if user.is_typing %}<span>typing...</span>{% endif %}
        {% endfor %}
    """

    # Configuration: ``presence = PresenceConfig(...)`` in the component's Meta
    @property
    def _presence_config(self) -> PresenceConfig:
        return self._meta.presence or PresenceConfig()  # type: ignore[attr-defined]

    # Internal state stored in __dict__ to avoid Pydantic serialization:
    # user_id -> presence_ref -> that connection's meta
    @property
    def _presence_registry(self) -> dict[str, dict[str, PresenceUser]]:
        if "__presence_registry" not in self.__dict__:
            self.__dict__["__presence_registry"] = {}
        return self.__dict__["__presence_registry"]

    @_presence_registry.setter
    def _presence_registry(self, value: dict[str, dict[str, PresenceUser]]) -> None:
        self.__dict__["__presence_registry"] = value

    # Whether this connection's own join came back: a producer on it announced it
    @property
    def _presence_announced(self) -> bool:
        return self.__dict__.get("__presence_announced", False)

    @_presence_announced.setter
    def _presence_announced(self, value: bool) -> None:
        self.__dict__["__presence_announced"] = value

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

    @staticmethod
    def _presence_merge(user_id: str, metas: dict[str, PresenceUser]) -> PresenceUser:
        """One user's entry, from the metas of their connections.

        The state is the liveliest one: typing if any connection types, else
        online if any is online. ``username``, ``metadata`` and ``last_active``
        are those of the meta heard from last.
        """
        latest = None
        for meta in metas.values():
            if latest is None or meta.last_active >= latest.last_active:
                latest = meta
        assert latest is not None
        states = {meta.state for meta in metas.values()}
        if PresenceState.TYPING in states:
            state = PresenceState.TYPING
        elif PresenceState.ONLINE in states:
            state = PresenceState.ONLINE
        else:
            state = PresenceState.OFFLINE
        return PresenceUser(
            user_id=user_id,
            username=latest.username,
            state=state,
            last_active=latest.last_active,
            metadata=latest.metadata,
        )

    @property
    def presence_users(self) -> list[PresenceUser]:
        """Get list of all tracked users (for template iteration)."""
        return [self._presence_merge(user_id, metas) for user_id, metas in self._presence_registry.items() if metas]

    @property
    def presence_online_count(self) -> int:
        """Get count of online users."""
        return sum(1 for u in self.presence_users if u.is_online())

    @property
    def presence_typing_users(self) -> list[PresenceUser]:
        """Get list of users currently typing."""
        return [u for u in self.presence_users if u.is_typing()]

    def presence_get_user(self, user_id: str) -> PresenceUser | None:
        """Get a specific user by ID."""
        metas = self._presence_registry.get(user_id)
        return self._presence_merge(user_id, metas) if metas else None

    def _presence_put(self, user_id: str, ref: str, data: dict[str, t.Any]) -> None:
        """Record what a connection said about its user, replacing its meta."""
        self._presence_registry.setdefault(user_id, {})[ref] = PresenceUser(
            user_id=user_id,
            username=data.get("username", ""),
            state=PresenceState(data.get("state", "online")),
            metadata=data.get("metadata", {}),
        )

    def _presence_drop(self, user_id: str, ref: str) -> None:
        """Forget a connection; the user goes with their last one."""
        metas = self._presence_registry.get(user_id)
        if metas is None:
            return
        metas.pop(ref, None)
        if not metas:
            del self._presence_registry[user_id]

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
        ref = _presence_ref(self)
        # The connection's producer may have announced it already: keep what it said
        if ref not in self._presence_registry.get(user_id, {}):
            self._presence_put(user_id, ref, {"username": username, "metadata": metadata or {}})

        # Request sync from other users
        if self._presence_config.sync_on_join:
            component = t.cast("Component", self)
            await component.broadcast(
                self._presence_channel(),
                action="presence_sync_request",
                requester_id=user_id,
                requester_ref=ref,
            )

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        """Handle presence notifications.

        Processes: presence_join, presence_leave, presence_typing,
        presence_sync_request, presence_sync_response

        Each names its connection in ``presence_ref``. One without it comes from a
        process older than #202, which keeps one meta per user: it is kept under
        ``LEGACY_REF``, so that process's join, typing and leave land on one meta of
        the user and leave the user's other connections alone.
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
        ref: str = kwargs.get("presence_ref") or LEGACY_REF

        if action == "presence_join" and user_id:
            self._presence_put(user_id, ref, kwargs)
            if (user_id, ref) == (self._presence_my_user_id(), _presence_ref(self)):
                self._presence_announced = True
            component.force_render()

        elif action == "presence_sync_response" and user_id:
            # A snapshot, only for a connection not heard from yet. Every change
            # a connection makes reaches us directly as well, and an answer can
            # be older than one: a tab that typed while the request sat in its
            # queue answers with the state from before (#202).
            if ref not in self._presence_registry.get(user_id, {}):
                self._presence_put(user_id, ref, kwargs)
                component.force_render()

        elif action == "presence_leave" and user_id:
            self._presence_drop(user_id, ref)
            component.force_render()

        elif action == "presence_typing" and user_id:
            meta = self._presence_registry.get(user_id, {}).get(ref)
            if meta is not None:
                meta.state = PresenceState(kwargs.get("state", "online"))
                meta.last_active = time.time()
            else:
                # Connection not tracked yet, add it
                self._presence_put(user_id, ref, kwargs)
            component.force_render()

        elif action == "presence_sync_request":
            # Another connection is asking who is here: answer for this one, once
            # its producer has announced it. A connection with a tracker alone
            # never says it joined, nor that it left: answering would put it on
            # the lists of those who came later for good. A request without
            # requester_ref (an older process) is answered too; such a process
            # drops answers about its own user.
            my_id = self._presence_my_user_id()
            my_ref = _presence_ref(self)
            mine = self._presence_registry.get(my_id, {}).get(my_ref)
            if kwargs.get("requester_ref") != my_ref and mine is not None and self._presence_announced:
                await component.broadcast(
                    channel,
                    action="presence_sync_response",
                    user_id=mine.user_id,
                    presence_ref=my_ref,
                    username=mine.username,
                    state=mine.state.value,
                    metadata=mine.metadata,
                )
