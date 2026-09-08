"""
Poll App Models

Simple poll/voting models demonstrating real-time vote counting.
"""

from django.db import models


class Poll(models.Model):
    """A poll with a question and multiple options."""

    question = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.question

    @property
    def total_votes(self):
        """Total number of votes across all options."""
        return sum(option.votes for option in self.options.all())


class Option(models.Model):
    """A poll option that users can vote for."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="options")
    text = models.CharField(max_length=200)
    votes = models.PositiveIntegerField(default=0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text

    @property
    def percentage(self):
        """Calculate vote percentage (0-100)."""
        total = self.poll.total_votes
        if total == 0:
            return 0
        return round((self.votes / total) * 100, 1)
