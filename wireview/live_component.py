"""LiveComponent - Stateful nested components with independent state.

LiveComponent provides Phoenix LiveView-style nested components that maintain
their own state while being rendered within a parent component.

Quick Start
===========

1. Define a LiveComponent:

    from wireview import LiveComponent

    class Counter(LiveComponent):
        _template_name = "counter.html"

        count: int = 0

        async def increment(self):
            self.count += 1

2. Use in parent template:

    {% load wireview %}
    <div {% tag_header %}>
        <h1>Dashboard</h1>
        {% live_component "Counter" id="counter-1" count=10 %}
    </div>

3. Handle events with @myself targeting:

    <!-- counter.html -->
    <div {% live_tag_header %}>
        <span>{{ count }}</span>
        <button {% on "click" "increment" myself=True %}>+1</button>
    </div>


Lifecycle
=========

1. **mount**: Called once when LiveComponent is first rendered
2. **update**: Called when parent re-renders with new assigns
3. **joined**: (inherited) Called after mount, similar to Component


Parent-Child Communication
==========================

**Parent to Child** (send_update):

    class Dashboard(Component):
        async def reset_counter(self):
            await self.send_update("counter-1", count=0)

**Child to Parent** (send_to_parent):

    class Counter(LiveComponent):
        async def increment(self):
            self.count += 1
            await self.send_to_parent("counter_changed", id=self.id, count=self.count)

    class Dashboard(Component):
        async def counter_changed(self, id: str, count: int):
            # Handle child event
            pass
"""

from __future__ import annotations

import typing as t

from pydantic import PrivateAttr

from .core.component import Component

if t.TYPE_CHECKING:
    from .repository import ComponentRepository

__all__ = ("LiveComponent",)


class LiveComponent(Component, public=False):
    """
    Stateful nested component with independent state.

    LiveComponent is designed to be rendered within a parent Component
    while maintaining its own state and event handling. Unlike regular
    Components that have their own WebSocket connection, LiveComponents
    share the parent's connection but handle their own events.

    Key differences from Component:
    - Rendered within parent's template using {% live_component %}
    - Events must use `myself=True` to target this component
    - Can communicate with parent via send_to_parent()
    - Parent can update via send_update()

    Attributes:
        _parent_id: ID of the parent Component (set automatically)
        _is_live_component: Marker to identify LiveComponents
    """

    # Parent component reference (set by repository)
    _parent_id: str | None = PrivateAttr(default=None)

    # Marker for identification
    _is_live_component: t.ClassVar[bool] = True

    # LiveComponents use a separate registry
    _live_all: t.ClassVar[dict[str, t.Type["LiveComponent"]]] = {}
    _live_by_fqn: t.ClassVar[dict[str, t.Type["LiveComponent"]]] = {}

    def __init_subclass__(
        cls: t.Type["LiveComponent"],
        name: str | None = None,
        public: bool = True,
    ) -> None:
        """Register LiveComponent in separate registries."""
        if public:
            name = name or cls.__name__
            fqn = f"{cls.__module__}.{name}"

            # Register in LiveComponent registries
            cls._live_all[name] = cls
            cls._live_by_fqn[fqn] = cls

            # Also register in Component registries for compatibility
            # This allows {% live_component "Counter" %} to work
            cls._all[name] = cls
            cls._by_fqn[fqn] = cls

            cls._name = name
            cls._fqn = fqn

        # Skip Component's __init_subclass__ public registration
        # but still do method validation
        for attr_name in vars(cls):
            attr = getattr(cls, attr_name)
            if not attr_name.startswith("_") and attr_name.islower() and callable(attr):
                from pydantic import validate_call

                try:
                    setattr(
                        cls,
                        attr_name,
                        validate_call(config={"arbitrary_types_allowed": True})(attr),
                    )
                except (NameError, TypeError):
                    pass

    @classmethod
    def _resolve_live(cls, name: str) -> t.Type["LiveComponent"]:
        """Resolve LiveComponent by name or FQN.

        Args:
            name: Component name or fully qualified name

        Returns:
            LiveComponent class

        Raises:
            LookupError: If component not found
        """
        # Try FQN first
        if name in cls._live_by_fqn:
            return cls._live_by_fqn[name]

        # Try simple name
        if name in cls._live_all:
            return cls._live_all[name]

        raise LookupError(f"LiveComponent '{name}' not found. " f"Available: {list(cls._live_all.keys())}")

    async def update(self, **assigns: t.Any) -> None:
        """Called when parent re-renders with new assigns.

        Override this method to handle prop changes. The default
        implementation updates matching attributes.

        Args:
            **assigns: New values from parent

        Example:
            class Counter(LiveComponent):
                count: int = 0
                label: str = "Count"

                async def update(self, **assigns):
                    # Custom logic before update
                    old_count = self.count
                    await super().update(**assigns)
                    if self.count != old_count:
                        await self.on_count_changed()
        """
        for key, value in assigns.items():
            if key in self.model_fields:
                setattr(self, key, value)

    async def send_to_parent(self, event: str, **kwargs: t.Any) -> None:
        """Send an event to the parent component.

        The parent component should have a method with the same name
        as the event to handle it.

        Args:
            event: Event name (method name on parent)
            **kwargs: Event arguments

        Example:
            # In LiveComponent
            async def save(self):
                await self.send_to_parent("item_saved", item_id=self.item.id)

            # In parent Component
            async def item_saved(self, item_id: int):
                self.saved_items.append(item_id)
        """
        if self._parent_id is None:
            return

        await self.wire.send_to_parent(self._parent_id, event, kwargs)

    @property
    def myself(self) -> str:
        """Return the ID for @myself targeting.

        Use this in templates for explicit targeting:
            <button {% on "click" "save" target=this.myself %}>Save</button>

        Or use the shorthand:
            <button {% on "click" "save" myself=True %}>Save</button>
        """
        return self.id

    def _render_live(self, repo: "ComponentRepository") -> str | None:
        """Render for live context (within parent).

        This adds the live component marker for client-side identification.
        """
        return self.wire.render(self, repo)


# Re-export for convenience
def is_live_component(component: Component) -> bool:
    """Check if a component is a LiveComponent."""
    return getattr(component.__class__, "_is_live_component", False)
