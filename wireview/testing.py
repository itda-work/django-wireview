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

from channels.layers import BaseChannelLayer
from django.contrib.auth.models import AnonymousUser

from .core.meta import WireviewMeta

if t.TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

    from .core.component import Component


__all__ = (
    "MountedComponent",
    "mount",
    "ComponentTestCase",
    "MockChannelLayer",
    "MockWireviewMeta",
)


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

    def __init__(self, params: dict[str, t.Any] | None = None):
        self._mock_channel_layer = MockChannelLayer()
        super().__init__(
            params=params or {},
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
        from django.shortcuts import resolve_url

        url = resolve_url(to, **kwargs)
        self._redirected_to = url
        self.freeze()
        await self.send("url_change", command="redirect", url=url)

    async def replace_to(self, to: t.Any, **kwargs: t.Any) -> None:
        """Override to track replace without checking channel_name."""
        from django.shortcuts import resolve_url

        url = resolve_url(to, **kwargs)
        await self.send("url_change", command="replace", url=url)

    async def push_to(self, to: t.Any, **kwargs: t.Any) -> None:
        """Override to track push without checking channel_name."""
        from django.shortcuts import resolve_url

        url = resolve_url(to, **kwargs)
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
    ):
        self.is_live = False
        self.user = user or AnonymousUser()
        self.params = params or {}


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
        session: Optional session data handed to the ``_on_mount`` hooks
        **initial_state: Initial field values for the component

    Returns:
        A MountedComponent wrapper with testing utilities

    Example:
        view = await mount(Counter, count=0)
        await view.call("increment")
        assert view.component.count == 1

        # With authenticated user
        view = await mount(Profile, user=my_user, name="Test")
    """
    from django.contrib.auth.models import AnonymousUser

    wire = MockWireviewMeta(params=params or {})
    repo = MockRepository(user=user, params=params or {})

    component = component_class(
        user=user or AnonymousUser(),
        wire=wire,  # type: ignore[arg-type]
        **initial_state,
    )

    mounted = MountedComponent(component, wire, repo)

    # The _on_mount hooks run before joined(), as they do on a real mount. A halt
    # skips joined(); the component is still returned so the test can assert on
    # what the hook did (a redirect, a frozen component).
    if await component._mount(params or {}, session):
        # Call joined() if it exists and is async
        if hasattr(component, "joined"):
            result = component.joined()
            if hasattr(result, "__await__"):
                await result

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
        **initial_state: t.Any,
    ) -> MountedComponent:
        """
        Mount a component for testing.

        See module-level mount() for full documentation.
        """
        return await mount(component_class, user=user, params=params, session=session, **initial_state)
