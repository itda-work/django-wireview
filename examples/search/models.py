"""
Search App Models

Book model for demonstrating live search functionality.
"""

from django.db import models


class Book(models.Model):
    """A book that can be searched."""

    title = models.CharField(max_length=200)
    author = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    isbn = models.CharField(max_length=13, blank=True)
    published_year = models.PositiveSmallIntegerField(null=True, blank=True)
    category = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} by {self.author}"
