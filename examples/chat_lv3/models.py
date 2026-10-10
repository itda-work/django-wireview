"""
Chat App Models (level 3)

The chat example's rooms and messages, plus who may be in a room: a message
belongs to a user, and a room to its members. A message can be edited by its
author, and is read a page at a time, newest first.
"""

from uuid import uuid4

from django.conf import settings
from django.db import models


class Room(models.Model):
    """Chat room where messages are exchanged. Only its members get in."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    """One user in one room. Who may read and write there."""

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="memberships")
    # Named after the app so a copy of this example (the next level) installs beside it
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["room", "user"], name="%(app_label)s_one_membership")]

    def __str__(self):
        return f"{self.user} in {self.room}"


class Message(models.Model):
    """Individual chat message in a room."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    # Who wrote it: the signed-in user of the connection that sent it, never a
    # name the browser supplies
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_messages",
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    # Set when the author changes the content; the list shows "(edited)"
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        # A page of the room's history is "the next N before this message",
        # newest first: created_at, and the id where two share a time
        indexes = [models.Index(fields=["room", "-created_at", "-id"], name="%(app_label)s_history")]

    def __str__(self):
        return f"{self.author}: {self.content[:50]}"
