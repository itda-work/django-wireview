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
import functools
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


def _digest(key: str) -> str:
    return hashlib.blake2b(key.encode(), digest_size=8, person=b"wv-presence").hexdigest()


def _presence_ref(component: t.Any) -> str:
    """The key of this component's browser tab among a user's metas (#202).

    The page names its load in the socket URL and keeps that name across its
    reconnects (``wire.tab_id``), so the join after a reconnect replaces the
    meta the tab had, and its next leave removes it, even when the process that
    heard the tab before died without sending its leave. The name comes from the
    client: the ref is a digest of it under the signed-in user, so a page that
    sends another's name speaks for a tab of its own user, never for theirs,
    and the name itself never travels. A page that names no tab (an older
    bundle) is keyed by its connection instead, as is one with no connection
    (``mount()`` in tests), by a ref of its own.

    Every component of one page gives the same ref, so its producer and tracker
    speak for one meta.
    """
    wire = component.wire
    tab = getattr(wire, "tab_id", "")
    if tab:
        user = getattr(component, "user", None)
        signed_in = getattr(user, "is_authenticated", False)
        return _digest(f"tab\0{user.pk if signed_in else ''}\0{tab}")  # type: ignore[union-attr]
    return _presence_connection(component)


def _presence_connection(component: t.Any) -> str:
    """The key of this component's WebSocket connection: a new one on every reconnect.

    A digest of ``connection_id``, not the id: that scopes the connection's
    uploads, and this travels to every subscriber of the topic. A tab's meta
    remembers which connection said it, so a late leave from the socket a tab
    reconnected away from does not remove what the new socket said.
    """
    connection_id = component.wire.connection_id
    if connection_id:
        return _digest(f"connection\0{connection_id}")
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
    presence state to others. Only a producer puts its user on other people's
    lists: between ``presence_join()`` and ``presence_leave()`` it also answers
    the sync request of a tracker that joins later, as Phoenix's ``track`` does
    (#202). It listens on its presence channel for that, whatever its own
    ``get_subscriptions()`` returns, and renders nothing for what it hears there.

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

    A subclass that overrides ``notification()`` passes presence messages on
    with ``super()``, or it does not answer sync requests.
    """

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__init_subclass__(**kwargs)
        # A producer has to hear its channel to answer a sync request, and the
        # usual override returns the component's own topics without super():
        # add the channel to whatever a subclass returns
        own = cls.__dict__.get("get_subscriptions")
        if own is None or getattr(own, "_presence_hears_its_channel", False):
            return

        @functools.wraps(own)
        def get_subscriptions(self: t.Any) -> set[str]:
            return own(self) | {self._presence_channel()}

        get_subscriptions._presence_hears_its_channel = True  # type: ignore[attr-defined]
        cls.get_subscriptions = get_subscriptions  # type: ignore[attr-defined]

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

    # Between presence_join() and presence_leave(): what it answers sync requests for
    @property
    def _presence_joined(self) -> bool:
        return self.__dict__.get("__presence_joined", False)

    @_presence_joined.setter
    def _presence_joined(self, value: bool) -> None:
        self.__dict__["__presence_joined"] = value

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | {self._presence_channel()}  # type: ignore[misc]

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
            "presence_conn": _presence_connection(self),
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

        self._presence_joined = True
        await component.broadcast(
            self._presence_channel(),
            action="presence_join",
            **self._presence_user_data(PresenceState.TYPING if self._presence_is_typing else PresenceState.ONLINE),
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

        self._presence_joined = False
        component = t.cast("Component", self)
        await component.broadcast(
            self._presence_channel(),
            action="presence_leave",
            user_id=self._presence_user_id(),
            presence_ref=_presence_ref(self),
            presence_conn=_presence_connection(self),
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

    async def _presence_answer(self, channel: str, request: dict[str, t.Any]) -> None:
        """Answer a sync request on this producer's channel with what it announced.

        Only while it is joined, and not to a request from its own tab, whose
        tracker records the tab itself. A request without ``requester_ref`` (an
        older process) is answered too; such a process drops answers about its
        own user.
        """
        if not self._presence_joined or channel != self._presence_channel():
            return
        if request.get("requester_ref") == _presence_ref(self):
            return
        state = PresenceState.TYPING if self._presence_is_typing else PresenceState.ONLINE
        await t.cast("Component", self).broadcast(
            channel, action="presence_sync_response", **self._presence_user_data(state)
        )

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        """Answer sync requests on the presence channel; render nothing for presence messages.

        On a class that also tracks (``PresenceTrackerMixin``), the tracker reads
        every presence message and has this answer, so a request gets one answer
        whichever mixin comes first.
        """
        action = kwargs.get("action", "")
        if not action.startswith("presence_") or isinstance(self, PresenceTrackerMixin):
            parent_notification = getattr(super(), "notification", None)
            if parent_notification is not None:
                await parent_notification(channel, **kwargs)
            return
        if action == "presence_sync_request":
            await self._presence_answer(channel, kwargs)
        # It hears the channel only to answer: nothing of it changed
        t.cast("Component", self).skip_render()


class PresenceTrackerMixin:
    """Mixin for components that track presence of other users.

    Keeps a meta per browser tab under each user, as Phoenix Presence does
    (#202): a user with two tabs open has two metas, and leaves the list only
    when the last of them leaves. The list the template reads has one entry per
    user, merged from that user's metas (see ``_presence_merge``). This mixin is
    for "consumer" components that display other users' presence.

    The metas are kept per presence channel too: a tracker that subscribes to
    several topics keeps each one's apart. ``presence_users`` lists a user once
    across all of them, and ``presence_users_in(topic)`` reads one.

    A tracker alone does not put its user on other people's lists: it neither
    announces a join nor a leave. A producer (``PresenceMixin``) does, and
    answers the sync requests of trackers that come later.

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
    # channel -> user_id -> presence_ref -> that tab's meta
    @property
    def _presence_registry(self) -> dict[str, dict[str, dict[str, PresenceUser]]]:
        if "__presence_registry" not in self.__dict__:
            self.__dict__["__presence_registry"] = {}
        return self.__dict__["__presence_registry"]

    @_presence_registry.setter
    def _presence_registry(self, value: dict[str, dict[str, dict[str, PresenceUser]]]) -> None:
        self.__dict__["__presence_registry"] = value

    # (channel, user_id, presence_ref) -> the connection that said the meta, when it named one
    @property
    def _presence_conns(self) -> dict[tuple[str, str, str], str]:
        if "__presence_conns" not in self.__dict__:
            self.__dict__["__presence_conns"] = {}
        return self.__dict__["__presence_conns"]

    def _presence_topic(self) -> str:
        """Return the presence topic/room identifier."""
        raise NotImplementedError("Subclass must implement _presence_topic()")

    def _presence_my_user_id(self) -> str:
        """Return current user's ID (to identify self)."""
        raise NotImplementedError("Subclass must implement _presence_my_user_id()")

    def _presence_channel(self, topic: str | None = None) -> str:
        """Get the broadcast channel for presence updates (of ``topic``, by default this one's)."""
        prefix = self._presence_config.channel_prefix
        return f"{prefix}.{self._presence_topic() if topic is None else topic}"

    @staticmethod
    def _presence_merge(user_id: str, metas: t.Iterable[PresenceUser]) -> PresenceUser:
        """One user's entry, from the metas of their tabs.

        The state is the liveliest one: typing if any tab types, else online if
        any is online. ``username``, ``metadata`` and ``last_active`` are those
        of the meta heard from last.
        """
        metas = list(metas)
        latest = None
        for meta in metas:
            if latest is None or meta.last_active >= latest.last_active:
                latest = meta
        assert latest is not None
        states = {meta.state for meta in metas}
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

    def _presence_by_user(self, channels: t.Iterable[str]) -> dict[str, list[PresenceUser]]:
        """The metas of ``channels``, by user, in the order the users were first heard of."""
        users: dict[str, list[PresenceUser]] = {}
        for channel in channels:
            for user_id, metas in self._presence_registry.get(channel, {}).items():
                users.setdefault(user_id, []).extend(metas.values())
        return users

    @property
    def presence_users(self) -> list[PresenceUser]:
        """Get list of all tracked users (for template iteration).

        One entry per user, across every topic this tracker hears.
        """
        users = self._presence_by_user(self._presence_registry)
        return [self._presence_merge(user_id, metas) for user_id, metas in users.items() if metas]

    def presence_users_in(self, topic: str) -> list[PresenceUser]:
        """The users of one topic (as ``_presence_topic()`` names it), one entry each.

        For a tracker that subscribes to several presence channels; a template
        reads it through a property of the component.
        """
        users = self._presence_by_user([self._presence_channel(topic)])
        return [self._presence_merge(user_id, metas) for user_id, metas in users.items() if metas]

    @property
    def presence_online_count(self) -> int:
        """Get count of online users."""
        return sum(1 for u in self.presence_users if u.is_online())

    @property
    def presence_typing_users(self) -> list[PresenceUser]:
        """Get list of users currently typing."""
        return [u for u in self.presence_users if u.is_typing()]

    def presence_get_user(self, user_id: str) -> PresenceUser | None:
        """Get a specific user by ID (across every topic this tracker hears)."""
        metas = self._presence_by_user(self._presence_registry).get(user_id)
        return self._presence_merge(user_id, metas) if metas else None

    def _presence_put(self, channel: str, user_id: str, ref: str, conn: str, data: dict[str, t.Any]) -> None:
        """Record what a tab said about its user, replacing its meta."""
        self._presence_registry.setdefault(channel, {}).setdefault(user_id, {})[ref] = PresenceUser(
            user_id=user_id,
            username=data.get("username", ""),
            state=PresenceState(data.get("state", "online")),
            metadata=data.get("metadata", {}),
        )
        self._presence_conns[(channel, user_id, ref)] = conn

    def _presence_drop(self, channel: str, user_id: str, ref: str) -> None:
        """Forget a tab; the user goes with their last one."""
        users = self._presence_registry.get(channel, {})
        metas = users.get(user_id)
        if metas is None:
            return
        metas.pop(ref, None)
        self._presence_conns.pop((channel, user_id, ref), None)
        if not metas:
            del users[user_id]
        if not users:
            self._presence_registry.pop(channel, None)

    def _presence_from_another_connection(self, channel: str, user_id: str, ref: str, conn: str) -> bool:
        """Whether a message comes from a socket the tab has since reconnected away from.

        Its meta names the connection that said it last. A late leave (or typing)
        from the one before -- the server noticed the dropped socket after the
        tab came back on a new one -- is not about what the tab says now.
        """
        heard = self._presence_conns.get((channel, user_id, ref), "")
        return bool(conn and heard and conn != heard)

    async def presence_track_self(
        self,
        username: str,
        metadata: dict[str, t.Any] | None = None,
    ) -> None:
        """Register self in the presence list and request sync.

        Call this in joined() to add current user to this component's own
        presence list and request presence info from other users. Other
        people's lists show this user only through a producer
        (``PresenceMixin.presence_join()``).
        """
        user_id = self._presence_my_user_id()
        ref = _presence_ref(self)
        channel = self._presence_channel()
        # The tab's producer may have announced it already: keep what it said
        if ref not in self._presence_registry.get(channel, {}).get(user_id, {}):
            self._presence_put(
                channel, user_id, ref, _presence_connection(self), {"username": username, "metadata": metadata or {}}
            )

        # Request sync from other users
        if self._presence_config.sync_on_join:
            component = t.cast("Component", self)
            await component.broadcast(
                channel,
                action="presence_sync_request",
                requester_id=user_id,
                requester_ref=ref,
            )

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        """Handle presence notifications.

        Processes: presence_join, presence_leave, presence_typing,
        presence_sync_request, presence_sync_response

        Each is recorded under the channel it came on. Each names its tab in
        ``presence_ref`` and its connection in ``presence_conn``. One without
        ``presence_ref`` comes from a process older than #202, which keeps one
        meta per user: it is kept under ``LEGACY_REF``, so that process's join,
        typing and leave land on one meta of the user and leave the user's other
        tabs alone.
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
        conn: str = kwargs.get("presence_conn") or ""

        if action == "presence_join" and user_id:
            # A tab joining again after a reconnect replaces what it said
            # before, a meta whose leave a dead process never sent among it
            self._presence_put(channel, user_id, ref, conn, kwargs)
            component.force_render()

        elif action == "presence_sync_response" and user_id:
            # A snapshot, only for a tab not heard from yet. Every change a tab
            # makes reaches us directly as well, and an answer can be older than
            # one: a tab that typed while the request sat in its queue answers
            # with the state from before (#202).
            if ref not in self._presence_registry.get(channel, {}).get(user_id, {}):
                self._presence_put(channel, user_id, ref, conn, kwargs)
                component.force_render()

        elif action == "presence_leave" and user_id:
            if not self._presence_from_another_connection(channel, user_id, ref, conn):
                self._presence_drop(channel, user_id, ref)
                component.force_render()

        elif action == "presence_typing" and user_id:
            meta = self._presence_registry.get(channel, {}).get(user_id, {}).get(ref)
            if meta is None:
                # Tab not tracked yet, add it
                self._presence_put(channel, user_id, ref, conn, kwargs)
                component.force_render()
            elif not self._presence_from_another_connection(channel, user_id, ref, conn):
                meta.state = PresenceState(kwargs.get("state", "online"))
                meta.last_active = time.time()
                component.force_render()

        elif action == "presence_sync_request":
            # Answered by the producers: they are what other people's lists
            # show (#202). One on this class answers here.
            if isinstance(self, PresenceMixin):
                await self._presence_answer(channel, kwargs)
