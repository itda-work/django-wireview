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
from .core.session import SessionView

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

    - ``push`` is a boosted navigation. The client fetches the destination and,
      **only if it stays inside the same live_session**, tells the server the new
      params. Crossing the boundary makes it an ordinary page load instead (#58).
    - ``replace`` only rewrites the address bar. Nothing is fetched and the body
      is untouched, so it is for the same page under a different query.
    - ``redirect`` navigates to another page: the destination is fetched and its
      components are mounted fresh.

    ``follow_push()`` and ``follow_redirect()`` reproduce the first and the third.
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

    Tracks all group messages and subscriptions for test assertions.

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
        self.sent_messages.append({"group": group, **message})

    async def group_add(self, group: str, channel: str) -> None:
        """Add a channel to a group."""
        self.groups.setdefault(group, []).append(channel)

    async def group_discard(self, group: str, channel: str) -> None:
        """Remove a channel from a group."""
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
            params=params or {},
            live_session=live_session,
            channel_name="test-channel",
            channel_layer=self._mock_channel_layer,
        )
        # Track calls for assertions
        self.sent_messages: list[dict[str, t.Any]] = []
        self.dom_actions: list[dict[str, t.Any]] = []

    @property
    def broadcasts(self) -> list[dict[str, t.Any]]:
        """Get all broadcast messages for assertions."""
        return self._mock_channel_layer.sent_messages

    @property
    def presence_broadcasts(self) -> list[dict[str, t.Any]]:
        """Get all presence-related broadcasts for assertions."""
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

    async def send_dom_action(
        self,
        action: t.Any,
        id: str,
        html: str,
    ) -> None:
        """Override to track DOM actions."""
        self.dom_actions.append({"action": action, "id": id, "html": html})


class MockRepository:
    """Mock repository for testing."""

    def __init__(
        self,
        user: "AbstractBaseUser | AnonymousUser | None" = None,
        params: dict[str, t.Any] | None = None,
        session: SessionView | None = None,
        live_session: t.Any = None,
    ):
        self.is_live = False
        self.user = user or AnonymousUser()
        self.params = params or {}
        self.session = session if session is not None else SessionView()
        self.live_session = live_session


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
    ):
        self._component = component
        self._wire = wire
        self._repo = repo

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
    def sent_messages(self) -> list[dict[str, t.Any]]:
        """Get all messages that would have been sent to the client."""
        return self.wire.sent_messages

    @property
    def dom_actions(self) -> list[dict[str, t.Any]]:
        """Get all DOM actions that would have been performed."""
        return self.wire.dom_actions

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
        *,
        params: dict[str, t.Any] | None = None,
        live_session: t.Any = _UNSET,
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
            **initial_state: initial field values, as in :func:`mount`.

        Raises:
            AssertionError: nothing redirected, the destination is not routed, or
                the destination's boundary refuses this user.
        """
        from .core.live_session import session_for_path

        nav = self._navigated("redirect", None, None)
        if live_session is _UNSET:
            if nav.path:
                try:
                    policy = session_for_path(nav.path)
                except Resolver404:
                    raise AssertionError(
                        f"{self._component._name} redirected to {nav.url!r}, which this project's URLconf "
                        f"does not serve. Pass live_session= to say which boundary the destination is in."
                    ) from None
            else:
                # A query-only redirect stays on the page it was already on.
                policy = self._repo.live_session
        else:
            policy = live_session

        if policy is not None and not await sync_to_async(policy.allows)(self._repo.user, self._repo.session):
            raise AssertionError(
                f"live_session {policy.name!r} refuses this user, so the server would answer the redirect "
                f"to {nav.url!r} with a login redirect or a 403 -- not with {component_class.__name__}."
            )

        return await mount(
            component_class,
            user=self._repo.user,
            params=nav.params if params is None else params,
            session=self._repo.session,
            live_session=policy,
            **initial_state,
        )

    async def follow_push(self) -> "Navigation":
        """Apply the last push or replace to this component, as the client would.

        The client updates the address bar and then tells the server the new
        params, which is what runs ``params_changed``. Doing that by hand means a
        test knows the wire protocol and still gets the repository's params
        wrong; this does both.

        A push that leaves the live_session is not this at all -- it becomes a
        full page load, and no ``params_changed`` is ever sent (#58). That case
        raises rather than running the callback the server would not run.

        Returns:
            The navigation that was followed.

        Raises:
            AssertionError: nothing pushed or replaced, or the push left the
                boundary.
        """
        from .core.live_session import session_for_path

        candidates = [n for n in self.navigations if n.command in ("push", "replace")]
        if not candidates:
            raise AssertionError(
                f"{self._component._name} neither pushed nor replaced the URL.\n{self._navigation_report()}"
            )
        nav = candidates[-1]

        if nav.command == "push" and nav.path:
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

        params = nav.params
        # The repository and every component's wire share one params dict, so the
        # server updates it in place rather than rebinding. Same here.
        self._repo.params.clear()
        self._repo.params.update(params)
        await self._component.params_changed(params, nav.url)
        return nav

    # Streams

    def stream_ops(self, stream: str | None = None) -> list[dict[str, t.Any]]:
        """Stream operations sent so far, optionally narrowed to one stream.

        Stream items are in neither :meth:`render` nor :attr:`dom_actions` -- the
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
        handler = getattr(self._component, handler_name, None)
        if handler is None:
            raise AttributeError(f"Component {self._component._name} has no handler '{handler_name}'")
        if not callable(handler):
            raise AssertionError(f"'{handler_name}' on {self._component._name} is not callable")

        result = handler(**kwargs)
        # Handle async handlers
        import inspect

        if inspect.iscoroutine(result):
            result = await result
        return result

    def render(self) -> str | None:
        """
        Render the component to HTML.

        Returns:
            The rendered HTML string, or None if frozen/redirected
        """
        return self._wire.render(self._component, self._repo)

    def clear_messages(self) -> None:
        """Clear the list of sent messages."""
        self._wire.sent_messages.clear()

    def clear_dom_actions(self) -> None:
        """Clear the list of DOM actions."""
        self._wire.dom_actions.clear()


async def mount(
    component_class: type["Component"],
    user: "AbstractBaseUser | AnonymousUser | None" = None,
    params: dict[str, t.Any] | None = None,
    session: t.Any = None,
    session_key: str | None = None,
    live_session: t.Any = None,
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
            and handed to the ``_on_mount`` hooks
        session_key: Optional session key, for code that identifies an anonymous
            visitor by ``self.session.session_key``
        live_session: The page boundary to mount inside, as a ``LiveSession`` (or
            its name). Without it the component mounts on a page that declares
            none, which is what refuses a component that named its
            ``_live_sessions``
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

        # Inside a page boundary, so the session hooks run and _live_sessions passes
        view = await mount(AdminPanel, user=staff, live_session="admin")
    """
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
    repo = MockRepository(user=user, params=param_map, session=session_view, live_session=policy)

    component = component_class(
        user=user or AnonymousUser(),
        wire=wire,  # type: ignore[arg-type]
        session=session_view,
        **initial_state,
    )

    mounted = MountedComponent(component, wire, repo)

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
        user: "AbstractBaseUser | AnonymousUser | None" = None,
        params: dict[str, t.Any] | None = None,
        session: t.Any = None,
        session_key: str | None = None,
        live_session: t.Any = None,
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
            **initial_state,
        )
