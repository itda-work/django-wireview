"""LiveComponent test app - Components for E2E testing."""

from wireview import Component, LiveComponent


class Counter(LiveComponent):
    """A counter LiveComponent that maintains independent state."""

    _template_name = "livecomp/counter.html"

    count: int = 0
    label: str = "Counter"

    async def joined(self):
        """Called when the LiveComponent joins."""
        pass

    async def update(self, **assigns):
        """Called when parent updates props."""
        for key, value in assigns.items():
            if key in self.model_fields:
                setattr(self, key, value)

    async def increment(self):
        """Increment the counter."""
        self.count += 1
        # Notify parent of change
        await self.send_to_parent("counter_changed", counter_id=self.id, count=self.count)

    async def decrement(self):
        """Decrement the counter."""
        self.count -= 1
        await self.send_to_parent("counter_changed", counter_id=self.id, count=self.count)

    async def reset(self):
        """Reset the counter to zero."""
        self.count = 0


class Dashboard(Component):
    """Parent component that contains multiple Counter LiveComponents."""

    _template_name = "livecomp/dashboard.html"

    total: int = 15  # Pre-calculated: 0 + 10 + 5
    last_changed: str = ""
    # Initialize counters with default values so they render in static HTML
    counters: list[dict] = [
        {"id": "counter-1", "label": "First Counter", "initial": 0},
        {"id": "counter-2", "label": "Second Counter", "initial": 10},
        {"id": "counter-3", "label": "Third Counter", "initial": 5},
    ]

    async def joined(self):
        """Initialize dashboard - counters already set as defaults."""
        pass

    async def counter_changed(self, counter_id: str, count: int):
        """Handle counter change events from LiveComponents."""
        self.last_changed = f"{counter_id}: {count}"
        # Update our record
        for c in self.counters:
            if c["id"] == counter_id:
                c["initial"] = count
                break
        self._recalculate_total()

    async def reset_all(self):
        """Reset all counters to zero."""
        for counter in self.counters:
            await self.send_update(counter["id"], count=0)
            counter["initial"] = 0
        self.total = 0
        self.last_changed = "All reset"

    async def sync_all(self, value: int = 5):
        """Set all counters to the same value."""
        for counter in self.counters:
            await self.send_update(counter["id"], count=value)
            counter["initial"] = value
        self.total = value * len(self.counters)
        self.last_changed = f"All synced to {value}"

    def _recalculate_total(self):
        """Recalculate total from all counters."""
        self.total = sum(c["initial"] for c in self.counters)
