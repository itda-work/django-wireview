"""
Chat App Models

Simple models for demonstrating real-time chat functionality.
"""

from uuid import uuid4

from django.db import models


class Room(models.Model):
    """Chat room where messages are exchanged."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Message(models.Model):
    """Individual chat message in a room."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    username = models.CharField(max_length=100)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.username}: {self.content[:50]}"
