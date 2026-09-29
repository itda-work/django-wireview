"""
Notifications App Models

Notification model for real-time notification demonstration.
"""

from django.conf import settings
from django.db import models


class NotificationType(models.TextChoices):
    """Types of notifications."""

    INFO = "info", "Information"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class Notification(models.Model):
    """A notification kept for one user until they read or dismiss it."""

    # The foreign key is what makes the notifications per user: with
    # AUTO_BROADCAST.related, saving a row announces it on the recipient's own
    # channel, "auth.user.<pk>.notifications" ({related model}.{pk}.{related_name}).
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=100)
    message = models.TextField()
    type = models.CharField(
        max_length=20,
        choices=NotificationType.choices,
        default=NotificationType.INFO,
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
