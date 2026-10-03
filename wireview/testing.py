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

import typing as t
from dataclasses import dataclass
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from channels.layers import BaseChannelLayer
from django.contrib.auth.models import AnonymousUser
from django.urls import Resolver404

from .core.meta import WireviewMeta
from .core.rendered import PROTOCOL_VERSION
from .core.session import SessionView
from .deprecation import warn_deprecated
from .repository import ComponentRepository

if t.TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

    from .core.component import Component


__all__ = (
    "MountedComponent",
    "Navigation",
    "mount",
    "ComponentTestCase",
    "MockChannelLayer",
    "MockWireviewMeta",
)


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
        self.sent_messages: list[dict[str, t.Any]] = []

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

    async def send(self, _command: str, **kwargs: t.Any) -> None:
        """Override to track messages instead of sending via WebSocket."""
        self.sent_messages.append({"type": _command, **kwargs})

    async def send_to(self, _channel: str, _command: str, **kwargs: t.Any) -> None:
        """Override to track messages instead of sending via WebSocket."""
        self.sent_messages.append({"channel": _channel, "command": _command, **kwargs})


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

    def for_render(self, root: "Component", live: bool = False) -> "MockRepository":
        """A repository holding only ``root``, as the first response's does when it
        reaches the root's template. Params are the same dict, not a copy.

        ``live`` renders as a socket does: the signed state is a dynamic part, and
        a LiveComponent child is a reference (``{"c": id}``) rather than its HTML.
        """
        repo = MockRepository(
            user=self.user, params=self.params, session=self.session, live_session=self.live_session, is_live=live
        )
        repo.register_component(root)
        if live:
            repo.begin_render(root.id)
        return repo


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

    async def _update_subscriptions(self) -> None:
        """Subscribe to what the component listens on now, as a session does after join
        and after each event: through the recorded layer, which refuses the names a
        real one refuses (``Meta.subscriptions = {"room:42"}`` fails the join)."""
        layer = self.wire._mock_channel_layer
        subscriptions = self._component.get_subscriptions()
        for group in subscriptions - self._subscriptions:
            await layer.group_add(group, "mounted")
        for group in self._subscriptions - subscriptions:
            await layer.group_discard(group, "mounted")
        self._subscriptions = subscriptions

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

        ``mount()`` stands in for the channel layer, so a broadcast is recorded here
        instead of reaching anyone.
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

        return await mount(
            component_class,
            user=self._repo.user,
            params=nav.params if params is None else params,
            session=self._repo.session,
            live_session=policy,
            path=nav.path or self._path,
            state=state,
            **initial_state,
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
        return await self._mount_destination(nav, verb, component_class, params, live_session, state, initial_state)

    async def _patch(self, nav: "Navigation") -> None:
        """Hand ``nav``'s params to this instance, as the client's ``params_changed`` does."""
        params = nav.params
        # The repository and every component's wire share one params dict, so the
        # server updates it in place rather than rebinding. Same here.
        self._repo.params.clear()
        self._repo.params.update(params)
        await self._component._handle_params(params, nav.url)
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
        from .repository import ComponentRepository
        from .utils import filter_parameters

        handler = getattr(self._component, handler_name, None)
        if handler is None:
            raise AttributeError(f"Component {self._component._name} has no handler '{handler_name}'")
        if not callable(handler):
            raise AssertionError(f"'{handler_name}' on {self._component._name} is not callable")
        # The checks a browser's event meets (repository.dispatch_event): a test must
        # not be able to call what no client can (#110).
        if not ComponentRepository._is_valid_event_handler(
            handler_name
        ) or not ComponentRepository._is_user_defined_method(self._component, handler_name):
            raise AssertionError(
                f"'{handler_name}' on {self._component._name} is not an event handler: a client cannot call it. "
                f"Call the component's method directly if the test means the method, not the event."
            )

        # The server runs attached handle_event hooks first; a test must not get past one.
        if (await self._component._run_hooks("handle_event", handler_name, kwargs)).get("halt"):
            return None

        # Arguments the handler does not take are dropped, as they are for an event
        # that carries a form's other fields.
        result = handler(**filter_parameters(handler, kwargs))
        # Handle async handlers
        import inspect

        if inspect.iscoroutine(result):
            result = await result
        await self._update_subscriptions()
        return result

    def render(self) -> str | None:
        """
        Render the component to HTML.

        Returns:
            The rendered HTML string, or None if frozen/redirected
        """
        return self._wire.render(self._component, self._repo.for_render(self._component))

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
        diff = await self._wire.render_diff(self._component, self._repo.for_render(self._component, live=True))
        self._component._clear_temporary_assigns()
        return diff

    def clear_messages(self) -> None:
        """Clear the list of sent messages."""
        self._wire.sent_messages.clear()


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
    from django.contrib.auth.models import AnonymousUser

    from .core.live_session import LiveSession, get_live_session

    # 1.1 had no path= option, and path=... set a field of that name. Taking it as
    # the page's path now would drop the field's value without a word (#169).
    # With the field in state=, path= can only be the page's.
    if path is not None and "path" in component_class.model_fields and "path" not in (state or {}):
        raise TypeError(
            f"mount() got path={path!r}, and {component_class.__name__} has a field called path: "
            f"path= is the page the component is on. Set the field with state={{'path': ...}}."
        )
    if state:
        if both := set(state) & set(initial_state):
            raise TypeError(f"mount() got {sorted(both)} both in state= and as keywords")
        initial_state = {**state, **initial_state}

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
    repo = MockRepository(user=user, params=param_map, session=session_view, live_session=policy)

    # Through new(), as a page builds it: the tutorials override new() to read
    # params, and a test that built the class directly skipped it (#119).
    component = component_class.new(
        user=user or AnonymousUser(),
        wire=wire,  # type: ignore[arg-type]
        session=session_view,
        **initial_state,
    )
    mounted = MountedComponent(component, wire, repo, path=urlsplit(path).path if path else None)

    # The mount hooks run before joined(), as they do on a real mount. A refusal
    # skips joined() and freezes the component, so ``render()`` here answers the
    # way the server would: with the redirect a hook queued, or with nothing. The
    # component is still returned so the test can assert on what the hook did.
    if await component._mount(param_map, session_view):
        # Call joined() if it exists and is async
        if hasattr(component, "joined"):
            result = component.joined()
            if hasattr(result, "__await__"):
                await result
        # Then, as the join does when the page's URL has a query, params_changed
        # with it (WireviewSession.command_join). A helper that skipped it had a
        # component mounted with params= miss what every page load runs.
        if param_map:
            await component._handle_params(dict(param_map), f"?{repo.get_query_string()}")
        await mounted._update_subscriptions()
    else:
        wire.freeze()

    return mounted


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
