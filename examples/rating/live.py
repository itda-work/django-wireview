"""
Rating App Components

This module demonstrates wireview's interactive UI patterns:
- wire.params for URL state persistence
- {% cond %} for conditional attributes
- Keyboard events (.key.ArrowLeft, .key.ArrowRight)
- Hover preview with mouse events
- Model subscriptions for real-time updates
"""

from wireview.component import Component
from wireview.schemas import ModelAction

from .models import Product, Rating


class XStarRating(Component):
    """
    Interactive star rating input (1-5 stars).

    Demonstrates:
    - wire.params for URL state persistence
    - Hover preview before clicking
    - Keyboard navigation (arrow keys)
    - {% cond %} for disabled states
    """

    _template_name = "rating/star_rating.html"
    _subscriptions = {"rating.rating"}

    product: Product
    current_rating: int = 0  # User's current rating (0 = not rated)
    hover_rating: int = 0  # Preview during hover (0 = not hovering)
    readonly: bool = False
    session_key: str = ""  # Will be set from request session

    async def joined(self):
        """
        Called when component mounts.

        Load existing rating from URL params or database.
        """
        # session_key arrives from the page (see views.py and detail.html): the
        # component state is the seam, wireview does not carry the Django session.
        self.session_key = self.session_key or "anonymous"

        # Try to restore from URL params first
        if rating_param := self.wire.params.get("rating"):
            try:
                self.current_rating = int(rating_param)
            except ValueError:
                pass

        # Or load from database
        if not self.current_rating:
            existing = await Rating.objects.filter(product=self.product, session_key=self.session_key).afirst()
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
            session_key=self.session_key,
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

    _template_name = "rating/rating_stats.html"
    _subscriptions = {"rating.rating"}

    product: Product

    async def mutation(self, channel: str, action: ModelAction, instance: Rating):
        """Update stats when ratings change."""
        if instance.product_id == self.product.id:
            self.force_render()
