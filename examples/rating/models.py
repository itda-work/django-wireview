"""
Rating App Models

Product and rating models for star rating demonstration.
"""

from django.db import models


class Product(models.Model):
    """A product that can be rated."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    image_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def average_rating(self):
        """Calculate average rating (0-5)."""
        ratings = self.ratings.all()
        if not ratings:
            return 0
        return sum(r.score for r in ratings) / len(ratings)

    @property
    def rating_count(self):
        """Total number of ratings."""
        return self.ratings.count()

    def rating_distribution(self):
        """Get count of each rating score."""
        distribution = {i: 0 for i in range(1, 6)}
        for rating in self.ratings.all():
            distribution[rating.score] += 1
        return distribution


class Rating(models.Model):
    """A rating for a product."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="ratings")
    score = models.PositiveSmallIntegerField(choices=[(i, f"{i} star{'s' if i > 1 else ''}") for i in range(1, 6)])
    session_key = models.CharField(max_length=40)  # Track by session
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ["product", "session_key"]

    def __str__(self):
        return f"{self.product.name}: {self.score} stars"
