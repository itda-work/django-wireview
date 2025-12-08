"""
Dashboard App Models

Models for demonstrating complex component composition and async loading.
"""

from uuid import uuid4

from django.db import models


class Stat(models.Model):
    """Dashboard statistics card data."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    change_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    icon = models.CharField(max_length=50, default="chart")
    color = models.CharField(max_length=20, default="blue")  # blue, green, red, yellow
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.label}: {self.value}"


class Activity(models.Model):
    """Dashboard activity feed entries."""

    TYPE_CHOICES = [
        ("login", "Login"),
        ("purchase", "Purchase"),
        ("upload", "Upload"),
        ("comment", "Comment"),
        ("update", "Update"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    user_name = models.CharField(max_length=100)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "activities"

    def __str__(self):
        return f"{self.type}: {self.description[:50]}"
