"""
Todo App Components

This module demonstrates wireview's core component patterns:
- Component class definition with Pydantic fields
- Model subscriptions for real-time ORM updates
- Event handlers with async methods
- Render control (skip_render, force_render, destroy)
"""

from enum import StrEnum

from wireview.component import Component
from wireview.schemas import ModelAction

from .models import Item


class Showing(StrEnum):
    """Filter state for todo items."""

    ALL = "all"
    COMPLETED = "completed"
    ACTIVE = "active"


class XTodoList(Component):
    """
    Main todo list container component.

    Demonstrates:
    - Class-level _subscriptions for model-wide notifications
    - mutation() hook for handling ORM changes
    - Properties for computed values
    - skip_render() to avoid redundant rendering
    - URL state management with wire.params
    """

    # Template path relative to TEMPLATES directories
    _template_name = "todo/list.html"

    # Subscribe to all Item model changes (create, update, delete)
    # Channel name matches the model name in lowercase
    _subscriptions = {"item"}

    # Pydantic field with default value - automatically validated
    showing: Showing = Showing.ALL

    @property
    def queryset(self):
        """Base queryset for all items."""
        return Item.objects.all()

    @property
    def items(self):
        """Filtered items based on current showing state."""
        return self.queryset

    async def mutation(
        self,
        channel: str,
        action: ModelAction,
        instance: Item,
    ):
        """
        Called when a subscribed model changes.

        Args:
            channel: The subscription channel (e.g., "item")
            action: ModelAction.CREATED, UPDATED, or DELETED
            instance: The affected model instance
        """
        if action == ModelAction.CREATED:
            # Force full re-render to show new item
            self.force_render()

    @property
    async def all_items_are_completed(self):
        """Async property for checking completion state."""
        return (await self.items.acount()) == (await self.items.completed().acount())

    async def toggle_all(self, toggle_all: bool):
        """Mark all items as completed or active."""
        await self.items.aupdate(completed=toggle_all)

    async def add(self, new_item: str):
        """
        Add a new todo item.

        Note: skip_render() is called because mutation() will handle
        the re-render when the new item is created.
        """
        await Item.objects.acreate(text=new_item)
        self.skip_render()  # Let mutation() handle the render

    async def show(self, showing: Showing):
        """
        Change the filter view.

        Updates both component state and URL params for bookmarkability.
        """
        self.showing = showing
        # Store in URL params so page refresh maintains state
        self.wire.params["showing"] = showing

    async def clear_completed(self):
        """Delete all completed items."""
        await self.items.completed().adelete()


class XTodoCounter(Component):
    """
    Counter showing active items count.

    Demonstrates:
    - Simple subscription to model changes
    - Auto-updates when any item changes
    """

    _template_name = "todo/counter.html"
    _subscriptions = {"item"}  # Re-renders on any item change

    @property
    def items(self):
        return Item.objects.all()


class XTodoItem(Component):
    """
    Individual todo item component.

    Demonstrates:
    - Dynamic subscriptions via @property
    - Instance-level subscriptions (per-item updates)
    - destroy() to remove component from DOM
    - focus_on() for focusing elements after render
    """

    _template_name = "todo/item.html"

    @property
    def _subscriptions(self):
        """
        Subscribe to changes for this specific item only.

        Returns a set of channels. Using @property allows dynamic
        subscription based on component state.
        """
        return {f"item.{self.item.id}"}

    # Django model field - automatically serialized/deserialized by PK
    item: Item
    editing: bool = False
    showing: Showing = Showing.ALL

    async def mutation(self, channel, instance: Item, action):
        """Handle changes to this specific item."""
        if action == ModelAction.DELETED:
            # Remove this component from the DOM
            await self.destroy()
        else:
            # Update local reference with new instance data
            self.item = instance

    async def delete(self):
        """Delete this item and remove component from DOM."""
        await self.item.adelete()
        await self.destroy()

    async def completed(self, completed: bool = False):
        """Toggle completion status."""
        self.item.completed = completed
        await self.item.asave()

    async def toggle_editing(self):
        """
        Enter edit mode.

        Demonstrates:
        - send_render() to immediately update DOM
        - focus_on() to focus input after render completes
        """
        if not self.item.completed:
            self.editing = not self.editing
        if self.editing:
            await self.send_render()
            # Focus the edit input after DOM updates
            await self.focus_on(f"#{self.id} input[name=text]")

    async def save(self, text):
        """Save edited text and exit edit mode."""
        self.item.text = text
        await self.item.asave()
        self.editing = False
