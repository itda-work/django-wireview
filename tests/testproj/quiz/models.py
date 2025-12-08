"""
Quiz App Models

Quiz, Question, Choice, and Submission models for interactive quiz demonstration.
"""

from django.db import models


class Quiz(models.Model):
    """A quiz containing multiple questions."""

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "quizzes"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Question(models.Model):
    """A question within a quiz."""

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    order = models.PositiveSmallIntegerField(default=0)
    explanation = models.TextField(blank=True, help_text="Shown after answering")

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:50]


class Choice(models.Model):
    """A possible answer for a question."""

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=200)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text


class Submission(models.Model):
    """A user's submission/attempt at a quiz."""

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="submissions")
    session_key = models.CharField(max_length=40)
    score = models.PositiveSmallIntegerField(default=0)
    total_questions = models.PositiveSmallIntegerField(default=0)
    completed_at = models.DateTimeField(auto_now_add=True)
    username = models.CharField(max_length=50, blank=True)  # Optional display name

    class Meta:
        ordering = ["-score", "completed_at"]

    def __str__(self):
        return f"{self.username or 'Anonymous'}: {self.score}/{self.total_questions}"

    @property
    def percentage(self):
        """Calculate score percentage."""
        if self.total_questions == 0:
            return 0
        return round((self.score / self.total_questions) * 100)
