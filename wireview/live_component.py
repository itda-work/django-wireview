"""LiveComponent - Stateful nested components with independent state.

LiveComponent provides Phoenix LiveView-style nested components that maintain
their own state while being rendered within a parent component.

Quick Start
===========

1. Define a LiveComponent:

    from wireview import LiveComponent

    class Counter(LiveComponent):
        class Meta:
            template_name = "counter.html"

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

The parent owns the child (docs/design/live-component-ownership.md). In a live
render the parent's template only names the child; after that template pass the
consumer runs, once per parent render:

1. **joined**: once per instance, when the child first appears in a render.
   The child's first HTML is rendered after it.
2. **update**: when the parent re-renders and a prop it passes differs from what
   it passed last time. Only the changed props are passed. State the child
   changed on its own is never reset by a parent render.
3. **leaving**: when the parent stops rendering the child, when the client
   reports the parent gone, or when the connection closes.

The children's diffs travel in the parent's ``render`` frame. An HTTP render is a
dead render: the child is inlined and none of these run.


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
from .debug import render_queries

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
    - Owned by the parent: never joins on its own, lives while the parent renders it
    - Can communicate with parent via send_to_parent()
    - Parent can update via send_update()

    Attributes:
        _parent_id: ID of the parent Component (set automatically)
        _is_live_component: Marker to identify LiveComponents
    """

    # Parent component reference (set by repository)
    _parent_id: str | None = PrivateAttr(default=None)

    # The props the parent template passed on its last render. A re-render
    # calls update() only for props whose value differs from these, so state
    # the child changed on its own is not reset by an unrelated parent render.
    _last_props: dict[str, t.Any] = PrivateAttr(default_factory=dict)

    # SlotContainer.content_key() of the slots the parent passed last time; a
    # different key on re-render means the child renders again.
    _last_slot_key: t.Any = PrivateAttr(default=None)

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
        from .core import render_reads
        from .core.component import _name_unlisted, _resolve_options, _validate_handlers

        cls._meta = _resolve_options(cls)
        render_reads.install(cls)
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
        else:
            _name_unlisted(cls, name)

        # Component's registration is skipped, its handler validation is not
        _validate_handlers(cls)

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

        raise LookupError(f"LiveComponent '{name}' not found. Available: {list(cls._live_all.keys())}")

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
            if key in type(self).model_fields:
                setattr(self, key, value)

    @classmethod
    async def update_many(cls, updates: "list[tuple[t.Self, dict[str, t.Any]]]") -> None:
        """Called once per parent render with every instance of this class whose props changed.

        Each entry is ``(component, assigns)``, the ``update()`` call it stands for.
        Override it to load what those components need in one query instead of one
        each -- Phoenix's ``update_many/1`` (GAP-035, #74). The default calls
        ``update()`` on each; an override that does not call it replaces it, so
        apply the assigns yourself (``await super().update_many(updates)`` does).

        Example:
            class Row(LiveComponent):
                item_id: int
                item: dict = {}

                @classmethod
                async def update_many(cls, updates):
                    ids = [assigns.get("item_id", c.item_id) for c, assigns in updates]
                    rows = {r.pk: r async for r in Item.objects.filter(pk__in=ids)}
                    await super().update_many(updates)
                    for component, _ in updates:
                        component.item = model_to_dict(rows[component.item_id])
        """
        for component, assigns in updates:
            await component.update(**assigns)

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


async def run_updates(
    updates: "t.Iterable[tuple[LiveComponent, dict[str, t.Any]]]",
    on_error: "t.Callable[[type[LiveComponent], LiveComponent | None, Exception], None]",
) -> None:
    """Deliver a parent render's prop changes: one ``update_many()`` per child class.

    A class that keeps the default gets each ``update()`` on its own, so one child
    that raises does not stop its siblings' updates, as before update_many existed.
    An override is called once, and what it raises is reported for the class.
    """
    groups: dict[type[LiveComponent], list[tuple[LiveComponent, dict[str, t.Any]]]] = {}
    for component, assigns in updates:
        groups.setdefault(type(component), []).append((component, assigns))
    for cls, group in groups.items():
        if getattr(cls.update_many, "__func__", None) is LiveComponent.update_many.__func__:  # type: ignore[attr-defined]
            for component, assigns in group:
                try:
                    with render_queries.scope("handler", component, "update"):
                        await component.update(**assigns)
                except Exception as e:
                    on_error(cls, component, e)
        else:
            try:
                with render_queries.scope("handler", group[0][0], "update_many"):
                    await cls.update_many(group)
            except Exception as e:
                on_error(cls, None, e)
