"""
Poll App Components

This module demonstrates wireview's basic patterns:
- Component state with Pydantic fields
- skip_render() for optimization
- {% class %} for conditional CSS classes
- Model subscriptions for real-time updates
- CSS loading states (.wireview-loading)
"""

from wireview.component import Component
from wireview.schemas import ModelAction

from .models import Option, Poll


class XPoll(Component):
    """
    Poll container showing question and options.

    Demonstrates:
    - Model subscriptions for real-time vote updates
    - skip_render() when broadcast handles update
    - {% class %} for conditional CSS classes
    - .wireview-loading CSS state during server calls
    """

    _template_name = "poll/poll.html"
    _subscriptions = {"poll.option"}  # Subscribe to all option changes

    poll: Poll
    voted_option_id: int | None = None  # Track which option user voted for

    async def joined(self):
        """
        Called when component mounts.

        Restore voted state from URL params if available.
        """
        if voted_id := self.wire.params.get("voted"):
            try:
                self.voted_option_id = int(voted_id)
            except ValueError:
                pass

    async def mutation(
        self,
        channel: str,
        action: ModelAction,
        instance: Option,
    ):
        """
        Handle option vote changes.

        When another user votes, this component receives the update
        and re-renders to show updated counts.
        """
        # Only react to updates for our poll's options
        if instance.poll_id == self.poll.id:
            self.force_render()

    async def vote(self, option_id: int):
        """
        Cast a vote for an option.

        Demonstrates:
        - skip_render() because mutation() will handle re-render
        - wire.params for URL state persistence
        """
        if self.voted_option_id is not None:
            # Already voted - ignore
            self.skip_render()
            return

        # Record the vote
        option = await Option.objects.aget(id=option_id)
        option.votes += 1
        await option.asave()

        # Remember user's choice and store in URL
        self.voted_option_id = option_id
        self.wire.params["voted"] = str(option_id)
        # Let mutation() handle the render from broadcast
