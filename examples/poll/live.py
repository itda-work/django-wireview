"""
Poll App Components

This module demonstrates wireview's basic patterns:
- Component state with Pydantic fields
- skip_render() for optimization
- {% class %} for conditional CSS classes
- Model subscriptions for real-time updates
- CSS loading states (.wireview-loading)
"""

import typing as t

from pydantic import Field, TypeAdapter, ValidationError

from wireview import Component, ModelAction

from .models import Option, Poll

#: An option's primary key as ``?voted=`` may say it: positive, and one a database column holds
OptionId = t.Annotated[int, Field(gt=0, lt=2**63)]


def _option_id_from_query(value: str | None) -> int | None:
    """The option id the address names, or None for a missing one or one that is not an id."""
    try:
        return TypeAdapter(OptionId).validate_python(value)
    except ValidationError:
        return None


class XPoll(Component):
    """
    Poll container showing question and options.

    Demonstrates:
    - Model subscriptions for real-time vote updates
    - skip_render() when broadcast handles update
    - {% class %} for conditional CSS classes
    - .wireview-loading CSS state during server calls
    """

    class Meta:
        template_name = "poll/poll.html"
        subscriptions = {"poll.option"}  # Subscribe to all option changes

    poll: Poll
    voted_option_id: int | None = None  # Track which option user voted for

    async def joined(self):
        """
        Called when component mounts.

        Restore voted state from URL params if available.
        """
        # The query is whatever the user typed: only an option of this poll counts.
        # Any other id would mark the poll voted and refuse the vote.
        voted_id = _option_id_from_query(self.wire.params.get("voted"))
        if voted_id is not None and await self.poll.options.filter(id=voted_id).aexists():
            self.voted_option_id = voted_id

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

        # Record the vote. The id comes from the browser, so it is looked up among
        # this poll's options only: any other id would count for another poll.
        option = await Option.objects.filter(id=option_id, poll_id=self.poll.id).afirst()
        if option is None:
            self.skip_render()
            return
        option.votes += 1
        await option.asave()

        # Remember user's choice and store in URL
        self.voted_option_id = option_id
        self.wire.params["voted"] = str(option_id)
        # Let mutation() handle the render from broadcast
