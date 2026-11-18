"""
Testing utilities for wireview components.

This module provides helpers for unit testing wireview components
without requiring a full WebSocket connection or browser.

Example:
    from wireview.testing import mount

    async def test_counter_increment():
        view = await mount(Counter, count=0)
        await view.call("increment")
        assert view.component.count == 1

    # Or using the ComponentTestCase mixin
    class TestCounter(ComponentTestCase):
        async def test_increment(self):
            view = await self.mount(Counter, count=0)
            await view.call("increment", amount=5)
            assert view.component.count == 5
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import itertools
import json
import re
import sys
import typing as t
import weakref
from dataclasses import dataclass
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from channels.layers import BaseChannelLayer
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.urls import Resolver404

from . import testing_dom
from .core import csrf, handlers, notifications, patches, shared_render
from .core.meta import WireviewMeta
from .core.rendered import PROTOCOL_VERSION
from .core.session import SessionView
from .debug import render_queries, render_queries_file
from .deprecation import warn_deprecated
from .features import presence
from .repository import ComponentRepository

if t.TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.http import HttpResponse
    from django.test import Client

    from .core.component import Component


__all__ = (
    "MountedComponent",
    "MountedChild",
    "Navigation",
    "mount",
    "ComponentTestCase",
    "MockChannelLayer",
    "MockWireviewMeta",
)


# A test's queries() block sees the SQL of connections opened from here on, whatever
# DEBUG_RENDER_QUERIES says: a test module is imported before the first query (#182)
render_queries.install()
# ...and a test process writes nothing for the editor (#188): what it renders is the test's
render_queries_file.suppress()

#: Sentinel for "work the boundary out from the URL" -- ``None`` is a real answer
#: (a page that declares no boundary), so it cannot double as "not given".
_UNSET = object()


@dataclass(frozen=True)
class Navigation:
    """One URL change the component asked the client to make.

    The three commands are not variations on one another:

    - ``push`` adds a history entry. On the page's own path -- another query or
      fragment -- it is a patch (#169): nothing is fetched and the components
      on the page hear ``params_changed``, keeping their state. Another path is
      fetched and its components join fresh; leaving the live_session makes that
      an ordinary page load (#58).
    - ``replace`` is the same without the history entry.
    - ``redirect`` navigates to another page, whatever the path: the destination
      is fetched and its components are mounted fresh.

    ``follow_push()`` reproduces the first two, ``follow_redirect()`` the third.
    """

    command: str
    url: str

    @property
    def path(self) -> str:
        """The path part, ``""`` for a query-only URL like ``?page=2``."""
        return urlsplit(self.url).path

    @property
    def params(self) -> dict[str, t.Any]:
        """The query string, parsed the way the repository parses it.

        Not ``parse_qs``: a test asserting on params should see the same values
        the component will, ``.json`` keys included.
        """
        from .repository import ComponentRepository

        return ComponentRepository.extract_params(urlsplit(self.url).query)


class MockChannelLayer(BaseChannelLayer):
    """Mock channel layer for testing broadcasts.

    Tracks all group messages and subscriptions for test assertions. A group name
    every channel layer refuses (``room:42``: only letters, digits, ``-``, ``_`` and
    ``.``, under 100 characters) raises the layer's ``TypeError`` here too -- recording
    it instead let a unit test pass for code that fails on the first real broadcast.

    Example:
        channel_layer = MockChannelLayer()
        # After component broadcasts...
        assert len(channel_layer.sent_messages) == 1
        assert channel_layer.sent_messages[0]["kwargs"]["action"] == "presence_join"
    """

    def __init__(self) -> None:
        self.groups: dict[str, list[str]] = {}
        self.sent_messages: list[dict[str, t.Any]] = []

    async def group_send(self, group: str, message: dict[str, t.Any]) -> None:
        """Record a group message."""
        self.require_valid_group_name(group)
        self.sent_messages.append({"group": group, **message})

    async def group_add(self, group: str, channel: str) -> None:
        """Add a channel to a group."""
        self.require_valid_group_name(group)
        self.groups.setdefault(group, []).append(channel)

    async def group_discard(self, group: str, channel: str) -> None:
        """Remove a channel from a group."""
        self.require_valid_group_name(group)
        if group in self.groups:
            self.groups[group] = [c for c in self.groups[group] if c != channel]

    def get_presence_broadcasts(self) -> list[dict[str, t.Any]]:
        """Get all presence-related broadcasts for assertions."""
        return [m for m in self.sent_messages if m.get("kwargs", {}).get("action", "").startswith("presence_")]

    def clear(self) -> None:
        """Clear all tracked messages and groups."""
        self.groups.clear()
        self.sent_messages.clear()


class _SentMessages(list[dict[str, t.Any]]):
    """``sent_messages``, which also keeps every stream operation apart (GAP-056).

    ``clear_messages()`` empties the list, and the page keeps its stream items:
    the DOM test helpers put on the page every operation sent since the mount.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stream_ops: list[dict[str, t.Any]] = []

    def _note(self, message: dict[str, t.Any]) -> None:
        if isinstance(message, dict) and message.get("type") == "stream_op":
            self.stream_ops.append(message)

    def append(self, message: dict[str, t.Any]) -> None:
        self._note(message)
        super().append(message)

    def extend(self, messages: t.Iterable[dict[str, t.Any]]) -> None:
        messages = list(messages)
        for message in messages:
            self._note(message)
        super().extend(messages)

    def insert(self, index: t.SupportsIndex, message: dict[str, t.Any]) -> None:
        self._note(message)
        super().insert(index, message)

    def __iadd__(self, messages: t.Iterable[dict[str, t.Any]]) -> t.Self:  # type: ignore[override]
        self.extend(messages)
        return self


class MockWireviewMeta(WireviewMeta):
    """
    Mock WireviewMeta for testing without WebSocket.

    Extends WireviewMeta to track sent messages and DOM actions
    for test assertions.
    """

    def __init__(self, params: dict[str, t.Any] | None = None, live_session: t.Any = None):
        self._mock_channel_layer = MockChannelLayer()
        super().__init__(
            params=params if params is not None else {},
            live_session=live_session,
            channel_name="test-channel",
            channel_layer=self._mock_channel_layer,
        )
        # Track calls for assertions
        self.sent_messages = _SentMessages()
        # Where a mounted component's mail goes: its connection's channel, read
        # once the message it is handling ends (MountedComponent._post)
        self._post: t.Callable[[dict[str, t.Any], int | None], None] | None = None

    @property
    def broadcasts(self) -> list[dict[str, t.Any]]:
        """Deprecated: ``view.broadcasts``. ``self.wire`` is not public beyond its navigation (#114)."""
        warn_deprecated("view.wire.broadcasts", "view.broadcasts")
        return self._mock_channel_layer.sent_messages

    @property
    def presence_broadcasts(self) -> list[dict[str, t.Any]]:
        """Deprecated: ``view.presence_broadcasts`` (#114)."""
        warn_deprecated("view.wire.presence_broadcasts", "view.presence_broadcasts")
        return self._mock_channel_layer.get_presence_broadcasts()

    def clone(self) -> "MockWireviewMeta":
        new_meta = MockWireviewMeta(params=self.params.copy())
        return new_meta

    async def redirect_to(self, to: t.Any, **kwargs: t.Any) -> None:
        """Override to track redirect without checking channel_name."""
        from .core.meta import resolve_destination

        url = resolve_destination(to, **kwargs)
        self._redirected_to = url
        self.freeze()
        await self.send("url_change", command="redirect", url=url)

    async def replace_to(self, to: t.Any, **kwargs: t.Any) -> None:
        """Override to track replace without checking channel_name."""
        from .core.meta import resolve_destination

        url = resolve_destination(to, **kwargs)
        await self.send("url_change", command="replace", url=url)

    async def push_to(self, to: t.Any, **kwargs: t.Any) -> None:
        """Override to track push without checking channel_name."""
        from .core.meta import resolve_destination

        url = resolve_destination(to, **kwargs)
        await self.send("url_change", command="push", url=url)

    async def send(self, _command: str, **kwargs: t.Any) -> bool:
        """Override to track messages instead of sending via WebSocket."""
        # A reset's Broadcast hold token is the session's, not the page's (#178):
        # it lets the held patches go where the reset is written
        hold = kwargs.pop("hold", None)
        message = {"type": _command, **kwargs}
        if self._post is not None:
            self._post(message, hold)
        else:
            self.sent_messages.append(message)
        return True

    async def send_to(self, _channel: str, _command: str, **kwargs: t.Any) -> bool:
        """Override to track messages instead of sending via WebSocket."""
        self.sent_messages.append({"channel": _channel, "command": _command, **kwargs})
        return True

    async def _send_broadcast(self, channel: str, **kwargs: t.Any) -> None:
        """Record the broadcast (``view.broadcasts``) and publish it as a page's is (#201).

        It reaches the sockets of this process through its channel layer and the
        components mounted in it, as ``abroadcast()`` does. Recorded first, with
        the check of the group name every layer makes.
        """
        from .core.transport import get_broker
        from .utils import _message, publish

        message = _message(channel, "notification", {"kwargs": kwargs})
        await self._mock_channel_layer.group_send(channel, message)
        await publish(get_broker(), message)


class _PageCsrf:
    """The CSRF secret of a mounted component's page, as the browser holds it in its cookie (#206).

    A page whose first render draws ``{% csrf_token %}`` sets the cookie, and its
    socket carries it: every render gets a token the form passes with. A mount
    stands for that page, so its renders draw one too -- a test that reads the
    form sees the ``csrfmiddlewaretoken`` input the browser shows.
    """

    def __init__(self) -> None:
        request = HttpRequest()
        get_token(request)  # a new secret, as the first response that draws a token makes one
        self.secret: str = request.META["CSRF_COOKIE"]
        #: The connection's token: masked once, shared by its live renders (``core/csrf.py``)
        self.token = csrf.mask(self.secret)


class MockRepository(ComponentRepository):
    """The repository a mounted component renders with: the HTTP first render's.

    It is the real repository, not an imitation, so the tags that build children
    during a render -- ``{% component %}`` and ``{% live_component %}`` -- draw them
    the way the page's first response does (#115). Like that response, it draws a
    LiveComponent child inline and runs none of its ``joined()``/``update()``: on a
    socket those belong to the consumer, which a mounted component does not have.

    Each ``MountedComponent.render()`` draws with a fresh one (:meth:`for_render`).
    A repository kept across renders would hand back the children it built the
    first time, and a LiveComponent child takes new props only through the
    ``update()`` that nothing here runs -- so it would draw the parent's old props.
    """

    def __init__(
        self,
        user: "AbstractBaseUser | AnonymousUser | None" = None,
        params: dict[str, t.Any] | None = None,
        session: SessionView | None = None,
        live_session: t.Any = None,
        is_live: bool = False,
        page_csrf: _PageCsrf | None = None,
    ):
        # A mounted component talks to the client this release ships.
        super().__init__(
            is_live=is_live,
            user=user,
            params=params,
            session=session,
            live_session=live_session,
            vsn=PROTOCOL_VERSION,
        )
        self._page_csrf = page_csrf or _PageCsrf()
        # An HTTP render is a response of its own: a token masked for it alone
        self._page_csrf_token = csrf.mask(self._page_csrf.secret)

    @property
    def csrf_token(self) -> t.Any:
        return self._page_csrf.token if self.is_live else self._page_csrf_token

    def _fresh_csrf_token(self) -> t.Any:
        # A stream item goes on the socket; it is masked anew, as a connection masks one
        return csrf.mask(self._page_csrf.secret)

    def for_render(self, root: "Component", live: bool = False) -> "MockRepository":
        """A repository holding only ``root``, as the first response's does when it
        reaches the root's template. Params are the same dict, not a copy.

        ``live`` renders as a socket does: the signed state is a dynamic part, and
        a LiveComponent child is a reference (``{"c": id}``) rather than its HTML.
        """
        repo = MockRepository(
            user=self.user,
            params=self.params,
            session=self.session,
            live_session=self.live_session,
            is_live=live,
            page_csrf=self._page_csrf,
        )
        # The query as the page last told it, which a followed push set (#210)
        repo._told_query = self._told_query
        repo.register_component(root)
        if live:
            repo.begin_render(root.id)
        return repo


class _HeldWire(MockWireviewMeta):
    """The wire of a component a mounted page holds beside the mounted one (GAP-056, #252).

    Built with a real wire's arguments (``Component._build``). What it sends
    goes to the mount's connection as the mounted component's does, so its
    stream operations, pushed events and navigations are in ``sent_messages``,
    and its mail to another component -- ``send_to_parent()``, ``send_update()``,
    a render it asks for -- is handled there, as a session handles it. Unlike
    the mounted component's, it keeps ``joined()``'s operations until its
    render, as a session's components do.
    """

    def __init__(self, **kwargs: t.Any) -> None:
        WireviewMeta.__init__(self, **kwargs)
        self._mock_channel_layer = kwargs.get("channel_layer") or MockChannelLayer()
        self.sent_messages = _SentMessages()
        self._post = None

    async def send(self, _command: str, **kwargs: t.Any) -> bool:
        return await WireviewMeta.send(self, _command, **kwargs)

    async def _do_send(self, _command: str, **kwargs: t.Any) -> bool:
        return await MockWireviewMeta.send(self, _command, **kwargs)


class _PageRepository(MockRepository):
    """The repository of a mounted component's page, held from one render to the next (GAP-056, #252).

    A connection keeps the components a page draws: the nested ones the page
    joins, the LiveComponents their parents own. This keeps them the same way,
    and its renders are a socket's -- a LiveComponent is a reference, settled
    after its parent's pass -- so the lifecycle the session runs runs here
    (``MountedComponent._settle``). What it renders is not the page's HTML:
    ``render()`` draws that from the instances held here (:meth:`redraw`).
    """

    wire_class = _HeldWire

    def __init__(self, mounted: "MountedComponent", repo: MockRepository) -> None:
        super().__init__(
            user=repo.user,
            params=repo.params,
            session=repo.session,
            live_session=repo.live_session,
            is_live=True,
            page_csrf=repo._page_csrf,
        )
        self._mounted = weakref.ref(mounted)
        self._told_query = repo._told_query
        self.channel_name = "test-channel"
        self.channel_layer = mounted.wire._mock_channel_layer
        # The passes of joined nested components that ran within the pass at hand,
        # with what each named: their LiveComponents settle with it (end_inline_pass)
        self.inline_passes: list[tuple[str, set[str]]] = []
        # For each drawer, the components its latest pass drew without an id, in
        # order: what a redraw draws there (a new pass would mint new ids)
        self.anonymous: dict[str, list[str]] = {}

    def _build_from(
        self, base: type["Component"], name: str, state: t.Any, id_given: bool | None = None
    ) -> "Component":
        component = super()._build_from(base, name, state, id_given=id_given)
        mounted = self._mounted()
        if mounted is not None:
            mounted._hold(component)
        return component

    def build(
        self,
        name: str,
        state: t.Any,
        drawer: "Component | None" = None,
        id_given: bool | None = None,
    ) -> "Component":
        component = super().build(name, state, drawer, id_given)
        if drawer is not None and not state.get("id"):
            self.anonymous.setdefault(drawer.id, []).append(component.id)
        return component

    def begin_render(self, parent_id: str) -> None:
        super().begin_render(parent_id)
        self.anonymous.pop(parent_id, None)

    def end_inline_pass(self, component_id: str, drawer_id: str | None, shown: t.Iterable[str] = ()) -> None:
        # A joined component drawn again within its drawer's pass: its
        # LiveComponents settle with the drawer's batch. One the page has yet
        # to join is left to that join, as on a connection.
        rendered = self.end_pass(component_id, shown)
        component = self.components.get(component_id)
        if component is not None and component.wire.has_joined:
            self.inline_passes.append((component_id, rendered))

    def redraw(self) -> "_RedrawRepository":
        """A repository for one HTTP render of the page as it is: these instances, as they are."""
        return _RedrawRepository(self)


class _RedrawRepository(MockRepository):
    """One ``render()`` of a held page: it draws the instances the page holds and changes none of them.

    A pass on a connection hands a nested component the props its drawer
    passes and queues a LiveComponent's ``update()``. A redraw is not a render
    the session makes, only the page as the browser shows it: a nested
    component shows what its own last render drew, its props included, until
    its drawer renders again. So the instances are drawn as they are. One the
    page does not hold yet -- the test changed the mounted component by hand --
    is drawn anew, as the first response draws it.
    """

    def __init__(self, held: _PageRepository) -> None:
        super().__init__(
            user=held.user,
            params=held.params,
            session=held.session,
            live_session=held.live_session,
            is_live=False,
            page_csrf=held._page_csrf,
        )
        self._told_query = held._told_query
        self.components = dict(held.components)
        self._anonymous = {drawer: list(ids) for drawer, ids in held.anonymous.items()}

    def build(
        self,
        name: str,
        state: t.Any,
        drawer: "Component | None" = None,
        id_given: bool | None = None,
    ) -> "Component":
        from .core.component import Component
        from .live_component import LiveComponent

        component_id = state.get("id")
        if not component_id and drawer is not None and (drawn := self._anonymous.get(drawer.id)):
            component_id = drawn.pop(0)
        held = self.components.get(component_id) if component_id else None
        if held is not None and not isinstance(held, LiveComponent) and type(held) is Component._resolve(name):
            return held
        return super().build(name, state, drawer, id_given)

    def build_live_component(
        self,
        name: str,
        state: t.Any,
        parent_id: str,
        slots: t.Any = None,
    ) -> t.Any:
        from .live_component import LiveComponent

        held = self.components.get(state.get("id") or "")
        if isinstance(held, LiveComponent) and type(held) is LiveComponent._resolve_live(name):
            return held
        return super().build_live_component(name, state, parent_id, slots)


class _Connection:
    """``notifications.Host`` for one mounted component: the connection it would be on."""

    def __init__(self, mounted: "MountedComponent") -> None:
        self._mounted = weakref.ref(mounted)

    @property
    def mounted(self) -> "MountedComponent":
        mounted = self._mounted()
        assert mounted is not None
        return mounted

    def _subscribed_to(self, channel: str) -> t.Iterator["Component"]:
        # Asked when its turn comes, as a session asks (notifications.deliver),
        # of every component the page holds
        for component in self.mounted._held_components():
            if channel in component.get_subscriptions() or channel in presence.presence_channels(component):
                yield component

    def _holds(self, component: "Component") -> bool:
        return not self.mounted.is_frozen

    async def send_render(self, component: "Component") -> None:
        """As a session renders after a notification: the diff goes out, when there is one."""
        mounted = self.mounted
        if component is not mounted.component:
            # A component the page holds besides the mounted one: its render settles what it draws
            await mounted._settle(component)
            return
        if (diff := await mounted.render_diff()) is not None:
            # Written at once, not mailed: ahead of what the receiver mailed
            self._record("render", id=component.id, diff=diff)
        if mounted._kept is not None:
            await mounted._settle(component)

    def _record(self, command: str, **payload: t.Any) -> None:
        self.mounted.wire.sent_messages.append({"type": command, **payload})

    async def _crashed(self, component: "Component") -> None:
        # A session would log it and join the component again; a test is told
        raise

    async def after_mutation_chores(self) -> None:
        await self.mounted._update_subscriptions()

    async def _awaits_join(self, component: "Component") -> None:
        # A mounted component joins as it mounts: nothing waits on one
        pass


class MountedComponent(t.Generic[t.TypeVar("C", bound="Component")]):
    """
    A wrapper for a mounted component that provides testing utilities.

    Provides access to the component instance and methods to simulate
    event handler calls.
    """

    def __init__(
        self,
        component: "Component",
        wire: MockWireviewMeta,
        repo: MockRepository,
        path: str | None = None,
    ):
        self._component = component
        self._wire = wire
        self._repo = repo
        # The path of the page it is on, when the test said: what tells a patch
        # from a navigation to another page (follow_push)
        self._path = path
        self._subscriptions: set[str] = set()
        # Broadcast frames held while a stream reset of the component is under
        # way, and the resets' tokens: a session writes them behind the reset (#178)
        self._held_frames: list[dict[str, t.Any]] = []
        self._held_tokens: set[int] = set()
        self._tokens = itertools.count(1)
        # A connection handles one message at a time. What a handler mails -- its
        # stream operations, push_event, JS, a reset's release -- and the
        # notifications that come meanwhile wait in its channel, in the order they
        # were sent, until the handler is done (#205, #201); a Broadcast patch
        # does not, it is written as it comes (_receive_patch).
        self._handling = 0
        self._channel: collections.deque[tuple[str, t.Any, int | None]] = collections.deque()
        # The loop of the test that mounted it: a component a finished test left
        # behind hears nothing published in the next one
        self._loop = asyncio.get_running_loop()
        # An event call() handled since the last render: a session renders after
        # each event, call() does not (render_diff() is the test's)
        self._unrendered_event = False
        # What the DOM helpers' user touched, form by form, as the page keeps it
        # across renders (FeedbackManager): an event's ``_unused`` (#244)
        self._touched = testing_dom.Touched()
        # The page as a connection holds it -- the nested components and
        # LiveComponents it draws, their lifecycle run -- from the first time a
        # helper reads it (_keep_page); until then render() draws anew each time
        self._kept: _PageRepository | None = None
        self._host = _Connection(self)
        wire._post = self._post
        wire.patch_gate = patches.Gate(self._hold_for_reset, self._let_reset_through)

    @contextlib.asynccontextmanager
    async def _handling_message(self) -> t.AsyncIterator[None]:
        """Handle one message: the mail it sends is written once it is over, as a connection's is."""
        self._handling += 1
        try:
            yield
        finally:
            self._handling -= 1
            if not self._handling:
                await self._read_channel()

    def _post(self, message: dict[str, t.Any] | None, hold: int | None, sender: "Component | None" = None) -> None:
        """Mail from the component: ``message`` for the page, ``hold`` the Broadcast token it lets go.

        ``sender`` is a component the page holds beside the mounted one. Its
        mail to a component -- ``send_to_parent()``, a render it asks for -- and
        any ``send_update()`` once the page is held are handled as a session
        handles them, in their turn (:meth:`_handle_mail`).
        """
        kind = message.get("type") if message is not None else None
        if self._kept is not None and (
            kind == "update_live_component" or (sender is not None and kind in ("dispatch_event", "send_render"))
        ):
            self._channel.append(("page", message, hold))
            if not self._handling:
                # Sent from no handler -- a task of the component's: its turn comes now
                asyncio.ensure_future(self._take_turn())
            return
        if self._handling:
            self._channel.append(("mail", message, hold))
        else:
            # Sent from no handler -- a test calling a method itself: nothing is ahead of it
            self._write(message, hold)

    async def _take_turn(self) -> None:
        async with self._handling_message():
            pass

    def _write(self, message: dict[str, t.Any] | None, hold: int | None) -> None:
        if message is not None:
            self.wire.sent_messages.append(message)
        if hold is not None:
            self._let_go(hold)

    async def _read_channel(self) -> None:
        """What waited in the channel, in order: mail is written, a notification handled."""
        while self._channel and not self._handling:
            kind, item, hold = self._channel.popleft()
            if kind == "mail":
                self._write(item, hold)
            elif kind == "page":
                await self._handle_mail(item)
            else:
                await self._handle_notification(item)

    async def _take_the_page(self) -> None:
        """What the page has, before a notification renders: the render after it is a diff against that.

        A session's join sent the whole page and the render after each event its
        diff, so a notification's render sends only what the notification
        changed. ``mount()`` drew none of them. Until a render (this, or the test's
        own ``render_diff()``) set the baseline, it is set here, unrecorded: the
        page already has it. Once the test set it, an event ``call()`` handled
        since is rendered here, and recorded, as its session sent it: what it
        changed, or nothing after ``skip_render()``, is not the notification's.
        """
        if self.wire._last_rendered is None:
            await self.render_diff()
        elif self._unrendered_event:
            await self._host.send_render(self._component)

    def _stop_listening(self) -> None:
        """Hear nothing more published in this process: no notification, no Broadcast."""
        patches.forget(self)
        notifications.forget(self)

    async def _hold_for_reset(self, component: "Component") -> int | None:
        token = next(self._tokens)
        self._held_tokens.add(token)
        return token

    async def _let_reset_through(self, component: "Component", token: int, tried: bool = True) -> None:
        # A reset that was sent carries its token and lets it go where it is written
        if not tried:
            self._let_go(token)

    def _let_go(self, token: int) -> None:
        self._held_tokens.discard(token)
        if not self._held_tokens:
            held, self._held_frames = self._held_frames, []
            self.wire.sent_messages.extend(held)

    async def _update_subscriptions(self) -> None:
        """Subscribe to what the component listens on now, as a session does after join
        and after each event: through the recorded layer, which refuses the names a
        real one refuses (``Meta.subscriptions = {"room:42"}`` fails the join)."""
        layer = self.wire._mock_channel_layer
        # A joined presence producer's connection hears its presence channel too (#202),
        # and every component the page holds subscribes on the one connection
        subscriptions: set[str] = set()
        for component in self._held_components():
            subscriptions |= component.get_subscriptions() | presence.presence_channels(component)
        for group in subscriptions - self._subscriptions:
            await layer.group_add(group, "mounted")
        for group in self._subscriptions - subscriptions:
            await layer.group_discard(group, "mounted")
        self._subscriptions = subscriptions

    def _receive_patch(self, message: dict[str, t.Any]) -> None:
        """A ``Broadcast`` published in this process, heard as a connection hears it (#178).

        The frames land in :attr:`sent_messages` as the component's own operations
        do, so :meth:`stream_ops`, :meth:`stream_html` and the rest read them alike.
        """
        if self.is_frozen:
            return
        for component in self._held_components():
            if type(component)._fqn != message["target"] or message["topic"] not in component.get_subscriptions():
                continue
            for frame in message["frames"]:
                if not patches.reaches(frame, self._repo.vsn):
                    continue
                sent = json.loads(patches.join(frame, component.id))
                received = {"type": sent["command"], **sent["payload"]}
                (self._held_frames if self._held_tokens else self.wire.sent_messages).append(received)

    async def _receive_notification(self, message: dict[str, t.Any]) -> None:
        """A notification or model mutation published in this process (#201).

        Heard when the component's connection is subscribed to its channel --
        what :meth:`_update_subscriptions` last settled -- and handled as a
        session handles it (``notifications.deliver``): the receiver, a render,
        the subscriptions settled again. While a handler runs it waits behind
        the mail sent so far, as in a connection's channel.
        """
        if self._loop is not asyncio.get_running_loop() or self.is_frozen:
            return
        if message["channel"] not in self._subscriptions:
            return
        self._channel.append(("notification", message, None))
        if not self._handling:
            await self._read_channel()

    async def _handle_notification(self, message: dict[str, t.Any]) -> None:
        async def handle() -> None:
            # Another connection's work, not the scope of whatever published it
            render_queries.detach()
            async with self._handling_message():
                if not self.is_frozen and message["channel"] in self._component.get_subscriptions():
                    await self._take_the_page()
                await notifications.deliver(self._host, notifications.RECEIVERS[message["type"]], message)

        await asyncio.ensure_future(handle())

    @property
    def component(self) -> "Component":
        """Access the wrapped component instance."""
        return self._component

    @property
    def wire(self) -> MockWireviewMeta:
        """Access the mock wire for inspecting sent messages."""
        # Use the component's wire, which is the same MockWireviewMeta we passed in
        return t.cast(MockWireviewMeta, self._component.wire)

    @property
    def broadcasts(self) -> list[dict[str, t.Any]]:
        """What the component broadcast, in order. Each has ``channel`` and ``kwargs``.

        Recorded here and published as a page's broadcast is (#201): the components
        mounted in this process hear it, the component itself included, and so do
        the sockets on the channel layer of the test settings -- a layer whose
        server is not running fails the ``call()`` that sent it.
        """
        return self.wire._mock_channel_layer.sent_messages

    @property
    def presence_broadcasts(self) -> list[dict[str, t.Any]]:
        """The broadcasts a ``PresenceMixin`` sent: join, leave, typing, sync."""
        return self.wire._mock_channel_layer.get_presence_broadcasts()

    @property
    def sent_messages(self) -> list[dict[str, t.Any]]:
        """Get all messages that would have been sent to the client."""
        return self.wire.sent_messages

    @property
    def is_frozen(self) -> bool:
        """Check if the component is frozen (won't re-render)."""
        return self.wire._is_frozen

    @property
    def redirected_to(self) -> str | None:
        """Get the URL the component redirected to, if any."""
        return self.wire._redirected_to

    # Navigation

    @property
    def navigations(self) -> list["Navigation"]:
        """Every URL change the component asked for, in order.

        The assertion helpers below cover the usual cases; reach for this when a
        test cares about the *sequence*.
        """
        return [
            Navigation(command=m["command"], url=m["url"]) for m in self.sent_messages if m.get("type") == "url_change"
        ]

    def _navigated(self, command: str, url: str | None, params: dict[str, t.Any] | None) -> "Navigation":
        from .repository import ComponentRepository

        candidates = [n for n in self.navigations if n.command == command]
        for nav in candidates:
            if url is not None:
                want = urlsplit(url)
                if nav.path != want.path:
                    continue
                if want.query and nav.params != ComponentRepository.extract_params(want.query):
                    continue
            if params is not None and nav.params != params:
                continue
            return nav

        wanted = url if url is not None else "any URL"
        if params is not None:
            wanted = f"{wanted} with params {params!r}"
        raise AssertionError(f"{self._component._name} did not {command} to {wanted}.\n{self._navigation_report()}")

    def _navigation_report(self) -> str:
        navigations = self.navigations
        if not navigations:
            return "It changed the URL not at all."
        lines = "\n".join(f"  {n.command} -> {n.url}" for n in navigations)
        return f"URL changes it did make:\n{lines}"

    def assert_pushed_to(self, url: str | None = None, *, params: dict[str, t.Any] | None = None) -> "Navigation":
        """Assert the component pushed to ``url``, and return that navigation.

        Args:
            url: expected destination. A query string in it is compared as
                *params*, so ``"/items/?page=2"`` and ``"/items/?page=2&"``
                match the same navigation. Omit to accept any destination.
            params: expected query params, compared whole. ``{}`` therefore
                asserts the destination has no query string at all.

        Raises:
            AssertionError: no push matched. The message lists every URL change
                the component did make.
        """
        return self._navigated("push", url, params)

    def assert_replaced_to(self, url: str | None = None, *, params: dict[str, t.Any] | None = None) -> "Navigation":
        """Assert the component replaced the URL with ``url``. See :meth:`assert_pushed_to`."""
        return self._navigated("replace", url, params)

    def assert_redirected_to(self, url: str | None = None, *, params: dict[str, t.Any] | None = None) -> "Navigation":
        """Assert the component redirected to ``url``. See :meth:`assert_pushed_to`."""
        return self._navigated("redirect", url, params)

    def assert_no_navigation(self, command: str | None = None) -> None:
        """Assert the component left the URL alone.

        The counterpart the positive assertions need: "it navigated" and "it
        navigated *here*" look the same from a test that only ever asserts the
        happy path.

        Args:
            command: narrow to one of ``"push"``, ``"replace"``, ``"redirect"``.
                Omit to require no URL change at all.
        """
        found = [n for n in self.navigations if command is None or n.command == command]
        if found:
            what = f"{command} anywhere" if command else "change the URL"
            lines = "\n".join(f"  {n.command} -> {n.url}" for n in found)
            raise AssertionError(f"{self._component._name} was not supposed to {what}, but did:\n{lines}")

    async def follow_redirect(
        self,
        component_class: type["Component"],
        /,
        *,
        params: dict[str, t.Any] | None = None,
        live_session: t.Any = _UNSET,
        state: dict[str, t.Any] | None = None,
        **initial_state: t.Any,
    ) -> "MountedComponent":
        """Follow the redirect and mount ``component_class`` on the destination page.

        A redirect is a page load: the browser fetches the destination and its
        components mount fresh. So this is a new :func:`mount`, not a continuation
        of this one -- but with the parts a test would otherwise have to restate:
        the destination's query string becomes the params, and the user and
        session carry over the way cookies do.

        **The destination's boundary is read from the URLconf**, not inherited
        from here, because a redirect is exactly how a page moves between
        boundaries. If that boundary refuses this user the server would answer
        with a login redirect or a 403 and never render the component, so this
        refuses too rather than mounting something the server would not.

        Args:
            component_class: the component to mount on the destination page.
            params: override the params taken from the redirect URL.
            live_session: override the boundary. Pass ``None`` for a page that
                declares none -- useful when the destination is not in this
                project's URLconf at all.
            state: initial field values as a dict, as in :func:`mount`.
            **initial_state: initial field values, as in :func:`mount`.

        Raises:
            AssertionError: nothing redirected, the destination is not routed, or
                the destination's boundary refuses this user.
        """
        nav = self._navigated("redirect", None, None)
        return await self._mount_destination(
            nav, "redirected", component_class, params, live_session, state, initial_state
        )

    async def _mount_destination(
        self,
        nav: "Navigation",
        verb: str,
        component_class: type["Component"],
        params: dict[str, t.Any] | None,
        live_session: t.Any,
        state: dict[str, t.Any] | None,
        initial_state: dict[str, t.Any],
    ) -> "MountedComponent":
        """Mount ``component_class`` on the page ``nav`` fetches, as the browser
        would: the destination's boundary from the URLconf, refused when that
        boundary refuses the user, the user and session carried over."""
        from .core.live_session import session_for_path

        if live_session is _UNSET:
            if nav.path:
                try:
                    policy = session_for_path(nav.path)
                except Resolver404:
                    raise AssertionError(
                        f"{self._component._name} {verb} to {nav.url!r}, which this project's URLconf "
                        f"does not serve. Pass live_session= to say which boundary the destination is in."
                    ) from None
            else:
                # A query-only URL stays on the page it was already on.
                policy = self._repo.live_session
        else:
            policy = live_session

        if policy is not None and not await sync_to_async(policy.allows)(self._repo.user, self._repo.session):
            raise AssertionError(
                f"live_session {policy.name!r} refuses this user, so the server would answer the move "
                f"to {nav.url!r} with a login redirect or a 403 -- not with {component_class.__name__}."
            )

        # The page's path goes in beside the fields, not through mount(path=): a
        # field called path is the test's to set here, as it was in 1.1 (#169).
        return await _mount(
            component_class,
            user=self._repo.user,
            params=nav.params if params is None else params,
            session=self._repo.session,
            session_key=None,
            live_session=policy,
            page_path=nav.path or self._path,
            fields=_merge_fields(state, initial_state),
        )

    @t.overload
    async def follow_push(self, /) -> "Navigation": ...

    @t.overload
    async def follow_push(
        self,
        component_class: type["Component"],
        /,
        *,
        params: dict[str, t.Any] | None = None,
        live_session: t.Any = _UNSET,
        state: dict[str, t.Any] | None = None,
        **initial_state: t.Any,
    ) -> "MountedComponent": ...

    async def follow_push(
        self,
        component_class: type["Component"] | None = None,
        /,
        *,
        params: dict[str, t.Any] | None = None,
        live_session: t.Any = _UNSET,
        state: dict[str, t.Any] | None = None,
        **initial_state: t.Any,
    ) -> "Navigation | MountedComponent":
        """Follow the last push or replace, as the browser would (#169).

        - **The page's own path** -- a query or fragment only, or the ``path``
          the test mounted with: a patch. The client changes the address bar
          and tells the server the new params, which runs ``params_changed`` on
          this same instance; what events changed stays. Call it with no
          arguments; it returns the :class:`Navigation`.
        - **Another path**: the client fetches it and the page's components
          join fresh, as after a redirect -- across a live_session boundary it
          is a full page load. Pass the component the destination renders; it
          is mounted there, as :meth:`follow_redirect` does, and returned.

        Which of the two the browser does is the helper's to say, not the
        test's: asking for the other one fails. A push to a URL with a path
        needs the path the component is on (``mount(..., path=)``) to tell
        the two apart. Without it the push is followed on this instance, as
        1.1 did, with a ``WireviewDeprecationWarning``; 2.0 fails there.

        Raises:
            AssertionError: nothing pushed or replaced; the call does not match
                what the browser would do; the destination is not routed or its
                boundary refuses the user.
        """
        candidates = [n for n in self.navigations if n.command in ("push", "replace")]
        if not candidates:
            raise AssertionError(
                f"{self._component._name} neither pushed nor replaced the URL.\n{self._navigation_report()}"
            )
        nav = candidates[-1]
        verb = "pushed" if nav.command == "push" else "replaced the URL with"

        if nav.path and self._path is None:
            if component_class is not None:
                raise AssertionError(
                    f"{self._component._name} {verb} {nav.url!r}. On the page's own path that is a patch, on "
                    f"another one a fetch of a new page -- say which page the component is on: "
                    f"mount(..., path=...)."
                )
            # 1.1 followed every push on this instance, and a push to the page's own
            # path written with the path is right to. Which page it left is unknown
            # here, so 1.1's answer stands until 2.0, with a warning (#169).
            warn_deprecated(
                "follow_push() after a push to a path, on a component mounted without path=",
                "mount(..., path=...) so follow_push() can tell a patch from a new page; without it 2.0 fails here",
            )
            if nav.command == "push":
                self._refuse_leaving_the_boundary(nav)
            await self._patch(nav)
            return nav
        if not nav.path or nav.path == self._path:
            if component_class is not None:
                raise AssertionError(
                    f"{self._component._name} {verb} {nav.url!r}, on the page it is on: nothing is fetched "
                    f"and this instance hears params_changed. Call follow_push() with no component."
                )
            await self._patch(nav)
            return nav

        if component_class is None:
            raise AssertionError(
                f"{self._component._name} {verb} {nav.url!r}, another path: the browser fetches it and "
                f"its components mount fresh (a full page load if it leaves the live_session). "
                f"Pass the component it renders: follow_push(Destination)."
            )
        destination = await self._mount_destination(
            nav, verb, component_class, params, live_session, state, initial_state
        )
        if not self._stays_for(destination):
            # The page it was on is gone: it hears nothing more, as a redirect's
            # component does (the redirect froze it)
            self.wire.freeze()
            self._stop_listening()
        return destination

    def _stays_for(self, destination: "MountedComponent") -> bool:
        """Whether this instance outlives the move to ``destination``'s page: a sticky
        component does across a boosted navigation, which stays in the live_session."""
        here, there = self._repo.live_session, destination._repo.live_session
        return self._component._meta.sticky and (here.name if here else "") == (there.name if there else "")

    async def _patch(self, nav: "Navigation") -> None:
        """Hand ``nav``'s params to this instance, as the client's ``params_changed`` does."""
        params = nav.params
        # The repository and every component's wire share one params dict, so the
        # server updates it in place rather than rebinding. Same here.
        self._repo.params.clear()
        self._repo.params.update(params)
        # As the address bar has it, for {% querystring %} (#210)
        self._repo.tell_query(urlsplit(nav.url).query)
        async with self._handling_message():
            await self._component._handle_params(params, nav.url)
            if self._kept is not None:
                # Every component the page holds hears the params, each rendered after (command_params_changed)
                others = [c for c in self._held_components()[1:] if c.wire.has_joined]
                await self._settle(self._component)
                for component in others:
                    if self._kept.reachable(component.id) is component:
                        await component._handle_params(params, nav.url)
                        await self._settle(component)
            # The session subscribes after params_changed as after any event
            await self._update_subscriptions()

    def _refuse_leaving_the_boundary(self, nav: "Navigation") -> None:
        """1.1's check for a push followed on this instance: a destination that is
        not routed, or that is in another live_session, is no patch (#58)."""
        from .core.live_session import session_for_path

        try:
            destination = session_for_path(nav.path)
        except Resolver404:
            raise AssertionError(
                f"{self._component._name} pushed to {nav.url!r}, which this project's URLconf does not "
                f"serve. A push fetches its destination, so the test needs a routed one."
            ) from None
        here = self._repo.live_session
        if (here.name if here else "") != (destination.name if destination else ""):
            left = repr(here.name) if here else "no boundary"
            entered = repr(destination.name) if destination else "no boundary"
            raise AssertionError(
                f"Pushing to {nav.url!r} leaves live_session {left} for {entered}, which is a full page "
                f"load: the client never sends params_changed. Mount the destination instead."
            )

    # Streams

    def stream_ops(self, stream: str | None = None) -> list[dict[str, t.Any]]:
        """Stream operations sent so far, optionally narrowed to one stream.

        Stream items are not in :meth:`render` -- the
        template renders an empty container and the items travel as their own
        messages. That is a protocol detail every project was re-deriving.
        """
        return [
            m
            for m in self.sent_messages
            if m.get("type") == "stream_op" and (stream is None or m.get("stream") == stream)
        ]

    def stream_items(self, stream: str | None = None) -> list[dict[str, str]]:
        """Every item sent by the stream operations, in order, as ``{"id", "html"}``."""
        return [item for op in self.stream_ops(stream) for item in op.get("items", [])]

    def stream_html(self, stream: str | None = None) -> str:
        """The HTML of every streamed item, concatenated.

        What a test that wants to say "the filtered list shows this and not that"
        actually needs. Remember :meth:`clear_messages` before the call under
        test: a handler that re-runs ``stream()`` adds to what came before.
        """
        return "".join(item.get("html", "") for item in self.stream_items(stream))

    async def call(self, handler_name: str, **kwargs: t.Any) -> t.Any:
        """
        Call an event handler on the component.

        Args:
            handler_name: Name of the handler method to call
            **kwargs: Arguments to pass to the handler

        Returns:
            The return value of the handler (if any)

        Raises:
            AttributeError: If the handler doesn't exist
            AssertionError: If the handler is not callable

        Example:
            await view.call("increment", amount=5)
            await view.call("save", text="Updated text")
        """
        return await self._handle(handler_name, kwargs)

    async def _handle(
        self,
        handler_name: str,
        kwargs: dict[str, t.Any],
        meta: t.Mapping[str, t.Any] | None = None,
        component: "Component | None" = None,
        args: t.Sequence[t.Any] = (),
    ) -> t.Any:
        """Run an event's handler as a session does: ``call()`` and the DOM helpers' one way in.

        ``meta`` is what a DOM helper's event says beyond the fields
        (``handlers.event_meta``): the handler gets the part it names.
        ``component`` is the one the event goes to, the mounted one unless a
        held page's element sent it to another (GAP-056, #252). On a held page
        the component renders after the handler, as a session renders it,
        and that settles what its render draws.
        """
        from .repository import ComponentRepository
        from .utils import filter_parameters

        component = component if component is not None else self._component
        handler = getattr(component, handler_name, None)
        if handler is None:
            raise AttributeError(f"Component {component._name} has no handler '{handler_name}'")
        if not callable(handler):
            raise AssertionError(f"'{handler_name}' on {component._name} is not callable")
        # The checks a browser's event meets (repository.dispatch_event): a test must
        # not be able to call what no client can (#110).
        if not ComponentRepository._is_valid_event_handler(
            handler_name
        ) or not ComponentRepository._is_user_defined_method(component, handler_name):
            raise AssertionError(
                f"'{handler_name}' on {component._name} is not an event handler: a client cannot call it. "
                f"Call the component's method directly if the test means the method, not the event."
            )

        async with self._handling_message():
            if component is self._component:
                self._unrendered_event = True
            # The server runs attached handle_event hooks first, in the same
            # message: what they mail waits with the handler's. A test must not
            # get past one.
            if (await component._run_hooks("handle_event", handler_name, kwargs)).get("halt"):
                return None
            # Arguments the handler does not take are dropped, as they are for an event
            # that carries a form's other fields.
            with render_queries.scope("handler", component, handler_name):
                result = handler(
                    *args, **{**filter_parameters(handler, kwargs), **handlers.named_event_meta(handler, meta)}
                )
                # Handle async handlers
                import inspect

                if inspect.iscoroutine(result):
                    result = await result
            if self._kept is not None:
                await self._settle(component)
            # Inside the event, as a session subscribes before it reads the next message
            await self._update_subscriptions()
        return result

    # The page it holds (GAP-056, #252)

    def _hold(self, component: "Component") -> None:
        """Connect a component the page's repository built to this mount's connection (``_HeldWire``)."""
        wire = component.wire
        if not isinstance(wire, _HeldWire):
            return
        wire.sent_messages = self._wire.sent_messages
        wire._mock_channel_layer = self._wire._mock_channel_layer
        wire.patch_gate = self._wire.patch_gate
        mounted = weakref.ref(self)

        def post(message: dict[str, t.Any] | None, hold: int | None) -> None:
            if (owner := mounted()) is not None:
                owner._post(message, hold, component)

        wire._post = post

    def _held_components(self) -> list["Component"]:
        """The mounted component, then every other one the page holds that a connection would reach."""
        if self._kept is None:
            return [self._component]
        others = [c for c in self._kept.reachable_components() if c is not self._component]
        return [self._component, *others]

    async def _keep_page(self) -> None:
        """Hold the page from here on, as a connection holds it: what the render draws, joined.

        The page's first response draws the nested components and the
        LiveComponents; then the page joins each nested component, and each
        render settles the LiveComponents its pass names (``send_render``). This
        does that once, the first time a helper reads the page; from then on each
        event renders the component it went to and settles what that draws.
        """
        if self._kept is not None or self.is_frozen:
            return
        self._kept = _PageRepository(self, self._repo)
        self._kept.register_component(self._component)
        async with self._handling_message():
            await self._settle(self._component)
            await self._update_subscriptions()

    def _target(self, component_id: str, what: str) -> "Component":
        """The component the page holds under ``component_id``, which an event from the page goes to."""
        if component_id == self._component.id:
            return self._component
        held = self._kept.reachable(component_id) if self._kept is not None else None
        if held is None or not held.wire.has_joined:
            raise testing_dom.DomError(
                f"{what} goes to {component_id!r}, which the page shows and no component is joined under: "
                "its join was refused, or it left the page"
            )
        return held

    async def _settle(self, component: "Component") -> None:
        """Render ``component`` as a session renders it after it handled something, and settle what it draws.

        ``WireviewSession.send_render``: its pass names its LiveComponents; a
        new one is mounted and joined (``joined()``, then the page's params), one
        whose props changed gets ``update()`` (``update_many()`` per class), one
        no longer named gets ``leaving()``, and each that ran a hook renders in
        turn. A nested component its pass draws for the first time is joined, as
        the page joins it; one it no longer draws leaves. A joined nested
        component drawn again gets the props its drawer passes.
        """
        from collections import deque

        from .live_component import LiveComponent, run_updates
        from .session import WireviewSession

        repo = self._kept
        assert repo is not None
        # Each render with how deep it is under a component the page joined: a
        # LiveComponent renders one deeper than its parent, as the session's
        # _render_level counts. Siblings are not counted: a list of a thousand rows is a page.
        limit = WireviewSession.MAX_LIVE_COMPONENT_DEPTH
        queue: deque[tuple[Component, int]] = deque([(component, 0)])
        while queue:
            current, depth = queue.popleft()
            if current is not self._component and repo.components.get(current.id) is not current:
                continue
            if current.wire._is_frozen:
                continue
            if depth >= limit:
                # Where the session logs and renders no further, the test fails: the page it would show is cut off
                raise AssertionError(
                    f"LiveComponents nest deeper than {limit} under {component._name}, at {current._name} "
                    f"{current.id!r} (WireviewSession.MAX_LIVE_COMPONENT_DEPTH): a template that draws a "
                    "component inside itself? The session renders nothing further down"
                )
            repo.inline_passes = []
            evaluated = await sync_to_async(self._pass)(current, repo)
            if isinstance(current.wire, _HeldWire):
                # What its joined() or update() sent goes behind its render
                await current.wire.flush_pending()
            if not evaluated:
                continue
            batch = repo.take_lifecycle(current.id)
            for inner_id, rendered in repo.inline_passes:
                repo._settle(inner_id, rendered, batch)
            leaving = list(batch.retired)
            for kept in batch.entombed:
                # The page lets go of what the render took away, and leaves it
                leaving += repo.bury(kept.id)
            await self._leave(leaving)
            refused: set[str] = set()
            for child in batch.new:
                if not await self._join(child):
                    refused.add(child.id)
            if batch.updates:
                for child, _props in batch.updates:
                    child.wire.enter_pending_mode()

                def _raise(_cls: t.Any, _component: t.Any, error: BaseException) -> None:
                    raise error

                await run_updates(batch.updates, _raise)
            # Then each child renders, and what it draws, before its next sibling: the session's _render_level
            # goes depth first through to_render (each child once)
            queue.extendleft(reversed([(c, depth + 1) for c in batch.to_render if c.id not in refused]))
            for nested in list(repo.components.values()):
                if nested is self._component or isinstance(nested, LiveComponent) or nested.wire.has_joined:
                    continue
                # The page joins what the render brought, in the order it is on the page: each join is a
                # render tree of its own, as the session's command_join is
                if await self._join(nested):
                    queue.append((nested, 0))

    def _pass(self, component: "Component", repo: _PageRepository) -> bool:
        """One template pass of ``component`` on the held page, as a socket renders it. Whether it ran."""
        repo.begin_render(component.id)
        try:
            component.wire.render(component, repo)
        except BaseException:
            # Nothing of it reached the page: what it stopped drawing is still there
            repo.render_failed()
            raise
        return component.wire.template_evaluated

    async def _join(self, component: "Component") -> bool:
        """Join ``component`` as the session does: its mount hooks, ``joined()``, then the page's params.

        Whether it joined. A refusal gives it up, as a session does; what
        ``joined()`` or a hook raises fails the test, where a session logs it.
        """
        repo = self._kept
        assert repo is not None
        component.wire.enter_pending_mode()
        try:
            mounted = await component._mount(repo.params, repo.session)
        except Exception:
            repo.abandon(component)
            component.wire.has_joined = True
            raise
        if not mounted:
            repo.abandon(component)
            component.wire.has_joined = True
            await component.wire.flush_pending()
            return False
        try:
            with render_queries.scope("joined", component):
                await component.joined()
        finally:
            component.wire.has_joined = True
        if repo.params:
            await component._handle_params(dict(repo.params), f"?{repo.get_query_string()}")
        return True

    async def _leave(self, components: list["Component"]) -> None:
        """``leaving()`` for each component the page let go that had joined, then its tasks cancelled."""
        for component in components:
            try:
                if component.wire.has_joined:
                    await component.leaving()
            finally:
                component._cancel_async_tasks()
                await presence.left(component, None)

    async def _handle_mail(self, message: dict[str, t.Any]) -> None:
        """Mail between the components of a held page, handled as a session handles it.

        ``send_to_parent()`` and ``wire.defer()`` (``dispatch_event``) run the
        handler on the instance they were sent for; ``send_update()`` runs the
        LiveComponent's ``update()`` (and is kept in ``sent_messages``, as it
        was); a render a component asks for renders it. Each renders what it
        changed and settles that.
        """
        from .live_component import LiveComponent

        repo = self._kept
        assert repo is not None
        kind = message["type"]
        async with self._handling_message():
            if kind == "dispatch_event":
                target = repo.reachable(message["id"])
                if target is None or target.wire.instance != message.get("instance"):
                    return
                await self._handle(
                    message["command"],
                    dict(message.get("kwargs") or {}),
                    component=target,
                    args=message.get("args") or (),
                )
            elif kind == "send_render":
                target = repo.reachable(message["id"])
                if target is not None and target.wire.instance == message.get("instance"):
                    await self._settle(target)
                    await self._update_subscriptions()
            elif kind == "update_live_component":
                self.wire.sent_messages.append(message)
                target = repo.reachable(message["live_component_id"])
                if (
                    not isinstance(target, LiveComponent)
                    or target.wire.instance != message.get("instance")
                    or target._parent_id != message["parent_id"]
                ):
                    return
                with render_queries.scope("handler", target, "update"):
                    await target.update(**message["assigns"])
                await self._settle(target)
                await self._update_subscriptions()

    async def child(self, component_id: str, /) -> "MountedChild":
        """The component the page holds under ``component_id``: a nested one, or a LiveComponent.

        Phoenix's ``find_live_child``. The page is held from here on, as the DOM
        helpers hold it: what the mounted component draws is joined and its
        LiveComponents' lifecycle runs. ``call()`` on what this returns runs a
        handler of that component as an event the page sends it; the page's
        elements reach it through :meth:`click` and the other helpers too.

        Raises:
            AssertionError: the page holds no component under that id. The
                message lists the ones it holds.
        """
        await self._keep_page()
        held = self._kept.reachable(component_id) if self._kept is not None else None
        if held is None or held is self._component:
            names = ", ".join(f"{c.id!r} ({c._name})" for c in self._held_components()[1:]) or "none"
            raise AssertionError(
                f"The page {self._component._name} draws holds no component under {component_id!r} "
                f"besides the mounted one. It holds: {names}"
            )
        return MountedChild(self, component_id)

    # The page's elements (GAP-056)

    async def click(
        self,
        selector: str,
        /,
        *,
        text: str | re.Pattern[str] | None = None,
        ctrl: bool = False,
        alt: bool = False,
        shift: bool = False,
        meta: bool = False,
    ) -> str | None:
        """Click the element ``selector`` picks in the render, as a user does, and send what the page would.

        The click bubbles from the element through its ancestors, and every
        ``{% on "click" %}`` binding it meets runs with its arguments and the
        fields the client serializes -- a binding the template does not have is
        not called. As in a browser, a checkbox or radio is ticked during the
        click and is followed by ``input`` and ``change`` unless the click is
        prevented, and a form's submit button then submits its form.

        Returns what :meth:`render` returns afterwards. Fails, saying why, when
        no element or more than one matches (narrow with a selector or
        ``text=``), when the element is disabled, when no binding of a server
        handler fires, when the binding is another component's, or when a
        ``JS()`` chain would push an event (no JavaScript runs here).

        ``ctrl``, ``alt``, ``shift`` and ``meta`` hold those keys down during
        the click, for the ``.ctrl``-style modifiers.

        Example:
            html = await view.click("button.remove", text="Delete")
            await view.click("li.item", ctrl=True)
        """
        held = testing_dom.modifier_keys(ctrl=ctrl, alt=alt, shift=shift, meta=meta)
        return await self._act(lambda page: page.click(page.find(selector, text), held))

    async def keydown(
        self,
        selector: str,
        key: str,
        /,
        *,
        text: str | re.Pattern[str] | None = None,
        ctrl: bool = False,
        alt: bool = False,
        shift: bool = False,
        meta: bool = False,
        composing: bool = False,
    ) -> str | None:
        """Press ``key`` on the element ``selector`` picks: dispatch ``keydown``, and send what the page would.

        ``key`` is the browser's ``event.key``: ``"Enter"``, ``"Escape"``, ``" "``,
        ``"a"``, ``"ArrowUp"``. The modifier keys hold for ``.ctrl``-style
        modifiers, and ``composing=True`` is a key an IME is composing with,
        which no key condition (``.enter``, ``.key_code``) lets through. As in a
        browser, Enter in a form's text field then submits the form (a click on
        its first submit button, or a submit when it has none and one text
        field). Nothing is typed: give a field its value with :meth:`input` or
        :meth:`change`. See :meth:`click` for what is sent and when it fails.

        Example:
            await view.keydown("input[name=draft]", "Enter")
        """
        return await self._key("keydown", selector, key, text, ctrl, alt, shift, meta, composing)

    async def keyup(
        self,
        selector: str,
        key: str,
        /,
        *,
        text: str | re.Pattern[str] | None = None,
        ctrl: bool = False,
        alt: bool = False,
        shift: bool = False,
        meta: bool = False,
        composing: bool = False,
    ) -> str | None:
        """As :meth:`keydown`, dispatching ``keyup``, which submits nothing.

        Example:
            await view.keyup("input[name=draft]", "Enter")   # {% on "keyup.enter" "send" %}
        """
        return await self._key("keyup", selector, key, text, ctrl, alt, shift, meta, composing)

    async def _key(
        self,
        type_: str,
        selector: str,
        key: str,
        text: str | re.Pattern[str] | None,
        ctrl: bool,
        alt: bool,
        shift: bool,
        meta: bool,
        composing: bool,
    ) -> str | None:
        if not isinstance(key, str) or not key:
            raise TypeError(f"{type_}() takes the key as the browser names it (event.key), like 'Enter' or 'a'")
        event = testing_dom.key_event(key, ctrl=ctrl, alt=alt, shift=shift, meta=meta, composing=composing)
        return await self._act(lambda page: page.key(page.find(selector, text), type_, event))

    async def focus(self, selector: str, /, *, text: str | re.Pattern[str] | None = None) -> str | None:
        """Give the element ``selector`` picks focus: dispatch ``focus`` (its own bindings) then ``focusin`` (bubbles).

        The element must be one a user can focus: a field, a button, a link
        with an href, or one with ``tabindex`` or ``contenteditable``. The
        helpers keep no focus between calls, so the element that had it gets no
        ``blur``. See :meth:`click` for what is sent and when it fails.

        Example:
            await view.focus("input[name=title]")
        """
        return await self._act(lambda page: page.focus(page.find(selector, text), "focus"))

    async def blur(self, selector: str, /, *, text: str | re.Pattern[str] | None = None) -> str | None:
        """Take focus from the element ``selector`` picks: ``blur`` (its own bindings) then ``focusout`` (bubbles).

        Leaving a field the user typed in sends ``change`` first in a browser:
        call :meth:`change` before this one for that.

        Example:
            await view.change("input[name=title]", "New")
            await view.blur("input[name=title]")
        """
        return await self._act(lambda page: page.focus(page.find(selector, text), "blur"))

    async def change(
        self, selector: str, /, value: t.Any = testing_dom._UNSET, *, text: str | re.Pattern[str] | None = None
    ) -> str | None:
        """Give the field ``selector`` picks a ``value`` and dispatch ``change`` at it, as leaving a typed field does.

        ``value`` is what the user leaves in it: a ``str`` for a text field, a
        textarea or a select (the option's value), a ``list[str]`` for a select
        multiple, ``True``/``False`` for a checkbox, ``True`` for a radio. Without
        it the field keeps the value the render gave it. Only ``change`` is
        dispatched; a browser also sends ``input`` while the user types.
        See :meth:`click` for what is sent and when it fails.

        Example:
            await view.change("select[name=sort]", "price")
        """
        return await self._act(lambda page: page.change(page.find(selector, text), value, "change"))

    async def input(
        self, selector: str, /, value: t.Any = testing_dom._UNSET, *, text: str | re.Pattern[str] | None = None
    ) -> str | None:
        """As :meth:`change`, dispatching ``input``: what each keystroke sends.

        Example:
            await view.input("input[name=query]", "wire")
        """
        return await self._act(lambda page: page.change(page.find(selector, text), value, "input"))

    async def submit(
        self,
        selector: str = "form",
        /,
        *,
        values: dict[str, t.Any] | None = None,
        submitter: str | None = None,
        text: str | re.Pattern[str] | None = None,
    ) -> str | None:
        """Submit the form ``selector`` picks, with ``values`` in its fields, and send what the page would.

        ``values`` maps a field's name to what the user put in it (the types
        :meth:`change` takes; a group of checkboxes takes the values to check as a
        list, a group of radio buttons the value to choose). A name the form does
        not have fails. ``submitter`` picks the submit button pressed, which must
        be the form's. The helpers keep nothing typed between calls: give every
        value the submit needs here. The submit must be prevented
        (``{% on "submit.prevent" %}``): one that is not would also load the
        form's action in a browser, and fails here.

        Example:
            await view.submit("form#signup", values={"email": "a@example.com"})
        """

        def act(page: testing_dom.Page) -> list[testing_dom.ServerEvent]:
            form = page.find(selector, text, what="form")
            button = page.find(submitter, what="submit button") if submitter is not None else None
            return page.submit(form, values, button)

        return await self._act(act)

    async def submit_form(
        self,
        selector: str = "form",
        /,
        *,
        values: dict[str, t.Any] | None = None,
        submitter: str | None = None,
        text: str | re.Pattern[str] | None = None,
        client: "Client | None" = None,
        follow: bool = False,
    ) -> "HttpResponse":
        """Send the form ``selector`` picks to its action over HTTP, as the browser's own submission does.

        The request is the one a page without script makes -- or one whose code
        calls ``form.submit()``: the form's method and action (the submit
        button's ``formmethod``/``formaction``/``formenctype`` win), against the
        page's address (``mount(..., path=...)`` and its params), and the fields
        in the order the page has them, each a field of its own even when two
        share a name. ``values`` and ``submitter`` as :meth:`submit` takes them; a
        submit button sends its name only when it is the one pressed. No submit
        event goes to a handler.

        It runs through Django's test ``Client`` -- the project's URLconf,
        middleware and view -- with CSRF checks on: the page's CSRF cookie (or
        session secret, with ``CSRF_USE_SESSIONS``) is the one the rendered
        ``{% csrf_token %}`` was masked from, the mount's user is logged in and
        its session data is in the session. Pass ``client`` to send with your own
        (its cookies are kept; the CSRF secret and login are added to them).

        Returns the response (``follow=True`` follows redirects, as the client
        does). A file field is sent empty: uploads are not driven here.

        Example:
            response = await view.submit_form("form#signup", values={"email": "a@example.com"})
            assert response.status_code == 302
        """
        page = await self._page()
        form = page.find(selector, text, what="form")
        button = page.find(submitter, what="submit button") if submitter is not None else None
        page_url = None
        if self._path is not None:
            query = self._repo.get_query_string()
            page_url = f"http://testserver{self._path}{'?' + query if query else ''}"
        sent = page.http_submission(form, values, button, page_url)
        return await sync_to_async(_send_submission)(sent, self, client, follow)

    async def hook_event(
        self,
        selector: str,
        event: str,
        payload: dict[str, t.Any] | None = None,
        /,
        *,
        hook: str | None = None,
        text: str | re.Pattern[str] | None = None,
    ) -> t.Any:
        """Push ``event`` from the hook on the element ``selector`` picks, as its ``this.pushEvent()`` does.

        The element must carry ``wire-hook``, and ``hook`` must name one of its
        hooks (it is needed only when it names several). The hook must be the
        mounted component's: the one whose element holds it. The component's
        ``handle_hook_event(hook_id, event, payload)`` runs, as a session runs
        it, with ``payload`` as JSON carries it.

        Returns the reply -- what ``handle_hook_event`` returned, as the hook's
        callback or Promise gets it -- and not the render: read that with
        :meth:`render`. A reply JSON cannot carry fails, as it fails on the socket.

        Example:
            reply = await view.hook_event("#chart", "point_clicked", {"x": 3})
            assert reply == {"handled": True}
        """
        import json

        page = await self._page()
        el = page.find(selector, text)
        owner_id, hook_id = page.hook_id(el, hook)
        try:
            sent = json.loads(json.dumps(payload or {}))
        except (TypeError, ValueError) as error:
            raise TypeError(f"A hook pushes JSON, and {payload!r} is not: {error}") from None
        component = self._target(owner_id, f"the hook of {el.describe()}")
        async with self._handling_message():
            if component is self._component:
                self._unrendered_event = True
            with render_queries.scope("handler", component, f"handle_hook_event({event!r})"):
                response = await component.handle_hook_event(hook_id, event, sent)
            if self._kept is not None:
                await self._settle(component)
            await self._update_subscriptions()
        try:
            return json.loads(json.dumps(response))
        except (TypeError, ValueError) as error:
            raise TypeError(
                f"{component._name}.handle_hook_event({event!r}) returned {response!r}, which the reply to the "
                f"hook cannot carry: it goes as JSON ({error})"
            ) from None

    async def _page(self) -> testing_dom.Page:
        """The page as the browser holds it now: the render, with the stream items in it."""
        await self._keep_page()
        # Off the loop, as a page's render is: a template may await an async property
        html = await sync_to_async(self.render)()
        if html is None:
            raise testing_dom.DomError(
                f"{self._component._name} is frozen and draws nothing: there is no page to act on"
            )
        return testing_dom.Page(html, self._component.id, self._wire.sent_messages.stream_ops, self._touched)

    async def _act(self, action: t.Callable[[testing_dom.Page], list[testing_dom.ServerEvent]]) -> str | None:
        """Do ``action`` on the page as it is now, send its events in order, and render."""
        from django.utils.datastructures import MultiValueDict

        from .utils import parse_request_data

        page = await self._page()
        # Every event of one action is read from the page before any answer, as
        # the browser dispatches them all before the server's render comes back
        events = action(page)
        targets = [
            self._target(event.component_id, f"the {event.event_type} binding of {event.element.describe()}")
            for event in events
        ]
        for event, target in zip(events, targets, strict=True):
            # What the session does with a user_event's two halves (command_user_event)
            kwargs = dict(parse_request_data(MultiValueDict(event.implicit_args)), **event.explicit_args)
            await self._handle(event.handler, kwargs, handlers.event_meta(event.meta), component=target)
        return await sync_to_async(self.render)()

    def render(self) -> str | None:
        """
        Render the component to HTML.

        With ``WIREVIEW["TEST_PAGE_CHECKS"]`` at ``"warn"`` or ``"raise"`` the
        page is checked for structural mistakes first (duplicate ids, a
        ``wire-auto-recover`` form that recovers nothing).

        Returns:
            The rendered HTML string, or None if frozen/redirected
        """
        repo = self._kept.redraw() if self._kept is not None else self._repo.for_render(self._component)
        html = self._wire.render(self._component, repo)
        if html is not None:
            self._check_page(html)
        return html

    def _check_page(self, html: str) -> None:
        """``TEST_PAGE_CHECKS``: say what is wrong with the page's structure, or nothing (the default)."""
        import warnings

        from .core.handlers import is_client_callable

        policies, skip_forms = _page_check_policy()
        if not any(policy != "ignore" for policy in policies.values()):
            return

        def callable_handler(component_id: str, name: str) -> bool | None:
            if component_id == self._component.id:
                return is_client_callable(self._component, name)
            held = self._kept.reachable(component_id) if self._kept is not None else None
            return is_client_callable(held, name) if held is not None else None

        page = testing_dom.Page(html, self._component.id, self._wire.sent_messages.stream_ops)
        problems = testing_dom.page_problems(page, callable_handler, skip_forms)
        for check, problem in problems:
            if policies[check] == "warn":
                warnings.warn(f"wireview TEST_PAGE_CHECKS: {problem}", UserWarning, stacklevel=3)
        raised = [f"{problem} ({check})" for check, problem in problems if policies[check] == "raise"]
        if raised:
            raise AssertionError(
                f"The page {self._component._name} draws has {len(raised)} problem(s) "
                '(WIREVIEW["TEST_PAGE_CHECKS"]):\n- ' + "\n- ".join(raised)
            )

    async def render_diff(self) -> dict[str, t.Any] | None:
        """What the next live render sends the client (#117).

        The diff, or ``None`` when nothing goes out: the render was skipped with
        ``skip_render()``, or nothing on the page changed. The first call is the
        full render and each later one is relative to the call before, as a page
        is. Like the consumer, it clears ``Meta.temporary_assigns`` afterwards.
        A LiveComponent child is a reference (``{"c": id}``); its own render is
        not included.

        ``render()`` always draws the whole page, so it cannot tell a handler
        that skipped the render its page needed -- a button left disabled -- from
        one that did not.
        """
        # A Meta.shared_render class is watched here as under DEBUG (#176)
        with shared_render.testing():
            diff = await self._wire.render_diff(self._component, self._repo.for_render(self._component, live=True))
        self._component._clear_temporary_assigns()
        self._unrendered_event = False
        return diff

    def clear_messages(self) -> None:
        """Clear the list of sent messages."""
        self._wire.sent_messages.clear()

    def queries(self) -> render_queries.Queries:
        """Collect the SQL the code inside the block runs: ``async with view.queries() as q:``.

        What ``call()``, ``render_diff()`` and ``render()`` run inside it is
        counted, with the tasks they start until the block ends, whatever
        ``DEBUG_RENDER_QUERIES`` says (#182). ``q.count`` is how many statements
        ran; ``q.assert_no_repeats()`` fails when one ran twice or more from the
        same template line or property -- an N+1 -- and its message lists where
        each statement came from. Blocks nest; an inner one's statements are the
        outer one's too.

        Example:
            view = await mount(Shelf)
            async with view.queries() as q:
                await view.call("refresh")
                await view.render_diff()
            assert q.count <= 3
            q.assert_no_repeats()
        """
        return render_queries.Queries()


class MountedChild:
    """A component the page of a mounted one holds: ``await view.child(id)`` (GAP-056, #252).

    It names the component by id and finds it on the page each time: a render
    that draws it anew under the id hands over the new instance, and one that
    takes it off the page leaves this naming nothing.
    """

    def __init__(self, mounted: MountedComponent, component_id: str) -> None:
        self._mounted = mounted
        self.id = component_id

    @property
    def component(self) -> "Component":
        """The instance the page holds under the id now."""
        held = self._mounted._kept.reachable(self.id) if self._mounted._kept is not None else None
        if held is None:
            raise AssertionError(f"The page holds no component under {self.id!r} any more: a render took it away")
        return held

    async def call(self, handler_name: str, **kwargs: t.Any) -> t.Any:
        """Run a handler of this component as the page's event does, then render it (``MountedComponent.call``)."""
        return await self._mounted._handle(handler_name, kwargs, component=self.component)


def _page_check_policy() -> tuple[dict[str, str], list[testing_dom.Selector]]:
    """``WIREVIEW["TEST_PAGE_CHECKS"]`` read: each check's policy, and the forms the recover check skips.

    One word sets every check; a dict sets each by name (a check it leaves out
    is ``"ignore"``) and may name, under ``"skip_forms"``, selectors of forms the
    ``recover_form`` check does not look at.
    """
    from django.core.exceptions import ImproperlyConfigured

    from . import settings as wireview_settings

    setting = wireview_settings.TEST_PAGE_CHECKS
    policies = ("ignore", "warn", "raise")
    if isinstance(setting, str):
        if setting not in policies:
            raise ImproperlyConfigured(
                f'WIREVIEW["TEST_PAGE_CHECKS"] is {setting!r}; it takes "ignore", "warn" or "raise", '
                "or a dict of them by check"
            )
        return dict.fromkeys(testing_dom.PAGE_CHECKS, setting), []
    if not isinstance(setting, dict):
        raise ImproperlyConfigured(
            f'WIREVIEW["TEST_PAGE_CHECKS"] is {setting!r}; it takes "ignore", "warn" or "raise", or a dict of them'
        )
    unknown = sorted(set(setting) - {*testing_dom.PAGE_CHECKS, "skip_forms"})
    if unknown:
        raise ImproperlyConfigured(
            f'WIREVIEW["TEST_PAGE_CHECKS"] names {unknown}, which are not checks; the checks are '
            f"{list(testing_dom.PAGE_CHECKS)}, and skip_forms lists forms the recover_form check skips"
        )
    chosen = {check: setting.get(check, "ignore") for check in testing_dom.PAGE_CHECKS}
    if wrong := {check: policy for check, policy in chosen.items() if policy not in policies}:
        raise ImproperlyConfigured(
            f'WIREVIEW["TEST_PAGE_CHECKS"] sets {wrong}; each check takes "ignore", "warn" or "raise"'
        )
    skip = setting.get("skip_forms", [])
    if isinstance(skip, str) or not isinstance(skip, (list, tuple)):
        raise ImproperlyConfigured('WIREVIEW["TEST_PAGE_CHECKS"]["skip_forms"] is a list of CSS selectors')
    try:
        selectors = [testing_dom.Selector(each) for each in skip]
    except ValueError as error:
        raise ImproperlyConfigured(f'WIREVIEW["TEST_PAGE_CHECKS"]["skip_forms"]: {error}') from None
    return chosen, selectors


def _send_submission(
    sent: testing_dom.Submission, mounted: MountedComponent, client: "Client | None", follow: bool
) -> "HttpResponse":
    """Make the request of ``sent`` with a test client that holds the mounted page's cookies."""
    import uuid
    from urllib.parse import urlencode, urlsplit

    from django.conf import settings as django_settings
    from django.middleware.csrf import CSRF_SESSION_KEY
    from django.test import Client

    if client is None:
        client = Client(enforce_csrf_checks=True)
    user = mounted._repo.user
    from django.contrib.auth.base_user import AbstractBaseUser

    if isinstance(user, AbstractBaseUser) and user.is_authenticated:
        client.force_login(user)
    data = dict(mounted._repo.session or {})
    secret = mounted._repo._page_csrf.secret
    if data or django_settings.CSRF_USE_SESSIONS:
        session = client.session
        session.update(data)
        if django_settings.CSRF_USE_SESSIONS:
            session[CSRF_SESSION_KEY] = secret
        session.save()
        # Signed-cookie sessions get a new cookie value when their contents change.
        if session.session_key is not None:
            client.cookies[django_settings.SESSION_COOKIE_NAME] = session.session_key
    if not django_settings.CSRF_USE_SESSIONS:
        client.cookies[django_settings.CSRF_COOKIE_NAME] = secret

    flat = [(name, "" if value is None else value) for name, value in sent.entries]
    if sent.method == "get":
        path = urlsplit(sent.action)._replace(query=urlencode(flat)).geturl()
        return t.cast("HttpResponse", client.get(path, follow=follow))
    if sent.enctype == "multipart/form-data":
        boundary = f"----wireview{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for name, value in sent.entries:
            quoted = name.replace("\r", "%0D").replace("\n", "%0A").replace('"', "%22")
            if value is None:
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted}"; filename=""\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n\r\n".encode()
                )
            else:
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted}"\r\n\r\n{value}\r\n'.encode()
                )
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        content_type = f"multipart/form-data; boundary={boundary}"
    elif sent.enctype == "text/plain":
        body = "".join(f"{name}={value}\r\n" for name, value in flat).encode()
        content_type = "text/plain"
    else:
        body = urlencode(flat).encode()
        content_type = "application/x-www-form-urlencoded"
    return t.cast("HttpResponse", client.post(sent.action, data=body, content_type=content_type, follow=follow))


async def mount(
    component_class: type["Component"],
    /,
    *,
    user: "AbstractBaseUser | AnonymousUser | None" = None,
    params: dict[str, t.Any] | None = None,
    session: t.Any = None,
    session_key: str | None = None,
    live_session: t.Any = None,
    path: str | None = None,
    state: dict[str, t.Any] | None = None,
    **initial_state: t.Any,
) -> MountedComponent:
    """
    Mount a component for testing.

    Creates a component instance with mock dependencies, suitable for
    unit testing without a WebSocket connection.

    Args:
        component_class: The component class to instantiate
        user: Optional user instance (defaults to AnonymousUser)
        params: Optional URL/query parameters
        session: Optional session data, read by the component as ``self.session``
            and handed to the ``Meta.on_mount`` hooks
        session_key: Optional session key, for code that identifies an anonymous
            visitor by ``self.session.session_key``
        live_session: The page boundary to mount inside, as a ``LiveSession`` (or
            its name). Without it the component mounts on a page that declares
            none, which is what refuses a component that named its
            ``Meta.live_sessions``
        path: The path of the page the component is on, like ``"/items/"``.
            Only :meth:`MountedComponent.follow_push` reads it: a push to this
            path is a patch, to another one a new page. A component with a field
            called ``path`` sets that field through ``state=``; ``path=`` without
            it in ``state=`` raises ``TypeError``, since 1.1 read it as the field
        state: Initial field values, as a dict. The keyword arguments below say
            the same thing more briefly, but they share a namespace with the
            options above: a field called ``params`` can only be given here. So
            can a field named like an option a later release adds -- the options
            are keyword-only so that adding one breaks nothing else (#119).
        **initial_state: Initial field values for the component

    Returns:
        A MountedComponent wrapper with testing utilities

    Example:
        view = await mount(Counter, count=0)
        await view.call("increment")
        assert view.component.count == 1

        # With authenticated user
        view = await mount(Profile, user=my_user, name="Test")

        # With a session
        view = await mount(Cart, session={"items": [1, 2]}, session_key="s1")

        # Inside a page boundary, so the session hooks run and Meta.live_sessions passes
        view = await mount(AdminPanel, user=staff, live_session="admin")
    """
    # 1.1 had no path= option, and path=... set a field of that name. Taking it as
    # the page's path now would drop the field's value without a word (#169).
    # With the field in state=, path= can only be the page's.
    if path is not None and "path" in component_class.model_fields and "path" not in (state or {}):
        raise TypeError(
            f"mount() got path={path!r}, and {component_class.__name__} has a field called path: "
            f"path= is the page the component is on. Set the field with state={{'path': ...}}."
        )
    return await _mount(
        component_class,
        user=user,
        params=params,
        session=session,
        session_key=session_key,
        live_session=live_session,
        page_path=path,
        fields=_merge_fields(state, initial_state),
    )


def _merge_fields(state: dict[str, t.Any] | None, initial_state: dict[str, t.Any]) -> dict[str, t.Any]:
    """The field values a test gave, from ``state=`` and the keywords together."""
    if not state:
        return initial_state
    if both := set(state) & set(initial_state):
        raise TypeError(f"mount() got {sorted(both)} both in state= and as keywords")
    return {**state, **initial_state}


async def _mount(
    component_class: type["Component"],
    *,
    user: "AbstractBaseUser | AnonymousUser | None",
    params: dict[str, t.Any] | None,
    session: t.Any,
    session_key: str | None,
    live_session: t.Any,
    page_path: str | None,
    fields: dict[str, t.Any],
) -> MountedComponent:
    """:func:`mount` once the options and the fields are told apart: ``fields`` is
    a dict, so no field name can collide with an option."""
    from django.contrib.auth.models import AnonymousUser

    from .core.live_session import LiveSession, get_live_session

    session_view = SessionView.wrap(session, session_key=session_key)
    policy = live_session if isinstance(live_session, LiveSession) or live_session is None else None
    if policy is None and live_session is not None:
        policy = get_live_session(str(live_session))
        if policy is None:
            raise LookupError(f"No live_session is declared under {live_session!r}")
    # One dict, shared: on a real connection the repository and every component's
    # wire hold the same params object, which is why the consumer updates it in
    # place. Two copies here would let ``follow_push()`` update one and render
    # from the other.
    param_map = dict(params or {})
    wire = MockWireviewMeta(params=param_map, live_session=policy)
    wire.id_given = bool(fields.get("id"))
    repo = MockRepository(user=user, params=param_map, session=session_view, live_session=policy)

    # Through new(), as a page builds it: the tutorials override new() to read
    # params, and a test that built the class directly skipped it (#119).
    component = component_class.new(
        user=user or AnonymousUser(),
        wire=wire,  # type: ignore[arg-type]
        session=session_view,
        **fields,
    )
    mounted = MountedComponent(component, wire, repo, path=urlsplit(page_path).path if page_path else None)
    # It hears a Broadcast published in this process for its class and topics (#178),
    # and the notifications on the channels it subscribes to (#201)
    patches.listen(mounted)
    notifications.listen(mounted)
    # ...until the test that mounted it ends (_end_of_test), or the fixture wider
    # than a test that did (the pytest plugin)
    plugin = sys.modules.get("wireview.pytest_plugin")
    if plugin is None or not plugin.kept(mounted):
        _mounted_in_test.add(mounted)

    # The mount hooks run before joined(), as they do on a real mount. A refusal
    # skips joined() and freezes the component, so ``render()`` here answers the
    # way the server would: with the redirect a hook queued, or with nothing. The
    # component is still returned so the test can assert on what the hook did.
    if await component._mount(param_map, session_view):
        # One message, as the join is: its resets' Broadcast patches come after
        # it, held from before joined() as a connection holds them
        async with mounted._handling_message():
            hold = await mounted._hold_for_reset(component) if component.get_subscriptions() else None
            # A broadcast joined() sends waits until the subscriptions are settled,
            # so the component hears its own, as the session's join does
            wire.enter_pending_mode()
            # Call joined() if it exists and is async
            if hasattr(component, "joined"):
                with render_queries.scope("joined", component):
                    result = component.joined()
                    if hasattr(result, "__await__"):
                        await result
            # As ComponentRepository.join marks it: what it hears from here goes
            # to its receiver (notifications.deliver, #207)
            wire.has_joined = True
            # Then, as the join does when the page's URL has a query, params_changed
            # with it (WireviewSession.command_join). A helper that skipped it had a
            # component mounted with params= miss what every page load runs.
            if param_map:
                await component._handle_params(dict(param_map), f"?{repo.get_query_string()}")
            await mounted._update_subscriptions()
            await wire.flush_pending()
            if hold is not None:
                # The join's release, behind what joined() mailed and the
                # notifications its broadcasts brought this connection
                mounted._post(None, hold)
    else:
        wire.freeze()

    return mounted


#: What mount() made in the test that is running: they stop hearing what is
#: published when it ends. Mounted components that never ran out of scope
#: otherwise live on until the collector finds them -- a test that shares an
#: event loop with the one before it would hear what that one left (#201).
_mounted_in_test: weakref.WeakSet[MountedComponent] = weakref.WeakSet()


def _end_of_test() -> None:
    """The test that mounted them is over: they hear nothing published after it.

    The pytest plugin (``wireview.pytest_plugin``) calls this when each test ends.
    What a fixture wider than a test mounted is not here: the plugin ends it with
    the fixture.
    """
    for mounted in list(_mounted_in_test):
        mounted._stop_listening()
    _mounted_in_test.clear()


class ComponentTestCase:
    """
    Mixin providing component testing utilities.

    Can be used with pytest or unittest test classes.

    Example with pytest:
        class TestCounter(ComponentTestCase):
            @pytest.mark.asyncio
            async def test_increment(self):
                view = await self.mount(Counter, count=0)
                await view.call("increment")
                assert view.component.count == 1

    Example with Django TestCase:
        class TestCounter(ComponentTestCase, django.test.TestCase):
            async def test_increment(self):
                view = await self.mount(Counter, count=0)
                await view.call("increment")
                assert view.component.count == 1
    """

    async def mount(
        self,
        component_class: type["Component"],
        /,
        *,
        user: "AbstractBaseUser | AnonymousUser | None" = None,
        params: dict[str, t.Any] | None = None,
        session: t.Any = None,
        session_key: str | None = None,
        live_session: t.Any = None,
        path: str | None = None,
        state: dict[str, t.Any] | None = None,
        **initial_state: t.Any,
    ) -> MountedComponent:
        """
        Mount a component for testing.

        See module-level mount() for full documentation.
        """
        return await mount(
            component_class,
            user=user,
            params=params,
            session=session,
            session_key=session_key,
            live_session=live_session,
            path=path,
            state=state,
            **initial_state,
        )
