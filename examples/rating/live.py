"""
Rating App Components

This module demonstrates wireview's interactive UI patterns:
- wire.params for URL state persistence
- {% cond %} for conditional attributes
- Keyboard events (.key.ArrowLeft, .key.ArrowRight)
- Hover preview with mouse events
- Model subscriptions for real-time updates
"""

import typing as t

from pydantic import Field, TypeAdapter, ValidationError

from wireview import Component, ModelAction

from .models import Product, Rating

#: A score a visitor can give: what ``?rating=`` may say
Score = t.Annotated[int, Field(ge=1, le=5)]


def _score_from_query(value: str | None) -> int | None:
    """The score the address names, or None for a missing one or one that is not 1 to 5.

    The query is whatever the user typed. ``?rating=7`` or ``?rating=-1`` would
    otherwise show a rating no one can give, over the one the visitor saved.
    """
    try:
        return TypeAdapter(Score).validate_python(value)
    except ValidationError:
        return None


class XStarRating(Component):
    """
    Interactive star rating input (1-5 stars).

    Demonstrates:
    - wire.params for URL state persistence
    - Hover preview before clicking
    - Keyboard navigation (arrow keys)
    - {% cond %} for disabled states
    """

    class Meta:
        template_name = "rating/star_rating.html"
        subscriptions = {"rating.rating"}

    product: Product
    current_rating: int = 0  # User's current rating (0 = not rated)
    hover_rating: int = 0  # Preview during hover (0 = not hovering)
    readonly: bool = False

    @property
    def visitor(self) -> str:
        """Identify an anonymous rater by the Django session.

        ``self.session`` is the read-only view of the session this component was
        mounted from; the view creates it so the key exists (see views.py).
        """
        return self.session.session_key or "anonymous"

    async def joined(self):
        """
        Called when component mounts.

        Load existing rating from URL params or database.
        """
        # Try to restore from URL params first
        if (score := _score_from_query(self.wire.params.get("rating"))) is not None:
            self.current_rating = score

        # Or load from database
        if not self.current_rating:
            existing = await Rating.objects.filter(product=self.product, session_key=self.visitor).afirst()
            if existing:
                self.current_rating = existing.score

    async def mutation(self, channel: str, action: ModelAction, instance: Rating):
        """Update when ratings change."""
        if instance.product_id == self.product.id:
            self.force_render()

    async def set_hover(self, star: int):
        """
        Set hover preview.

        Shows preview of what rating would look like.
        """
        if self.readonly:
            self.skip_render()
            return
        self.hover_rating = star

    async def clear_hover(self):
        """Clear hover preview."""
        self.hover_rating = 0

    async def rate(self, score: int):
        """
        Submit a rating.

        Demonstrates:
        - Database save with upsert pattern
        - wire.params for URL persistence
        """
        if self.readonly:
            self.skip_render()
            return

        # Validate score
        if not 1 <= score <= 5:
            self.skip_render()
            return

        # Upsert rating
        rating, created = await Rating.objects.aupdate_or_create(
            product=self.product,
            session_key=self.visitor,
            defaults={"score": score},
        )

        self.current_rating = score
        self.hover_rating = 0

        # Persist to URL
        self.wire.params["rating"] = str(score)

    async def adjust_rating(self, delta: int):
        """
        Adjust rating with keyboard arrows.

        Demonstrates keyboard event handling.
        """
        if self.readonly:
            self.skip_render()
            return

        new_rating = max(1, min(5, (self.current_rating or 3) + delta))
        await self.rate(new_rating)


class XRatingStats(Component):
    """
    Display rating statistics and distribution.

    Demonstrates:
    - Model subscriptions for real-time updates
    - Computed properties for statistics
    """

    class Meta:
        template_name = "rating/rating_stats.html"
        subscriptions = {"rating.rating"}

    product: Product

    async def mutation(self, channel: str, action: ModelAction, instance: Rating):
        """Update stats when ratings change."""
        if instance.product_id == self.product.id:
            self.force_render()
