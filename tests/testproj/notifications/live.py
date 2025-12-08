"""
Notifications App Components

This module demonstrates wireview's advanced communication patterns:
- broadcast() / abroadcast() for sending messages
- notification() hook for receiving custom events
- push_js() with JS() command builder
- Streams API for notification list
- JS().show(), JS().hide(), JS().toggle(), JS().transition()
"""

from wireview.component import Component
from wireview.js import JS
from wireview.schemas import ModelAction

from .models import Notification, NotificationType


class XNotificationBell(Component):
    """
    Notification bell icon with unread count badge.

    Demonstrates:
    - notification() hook for custom events
    - broadcast() to send messages to other components
    - push_js() with JS() commands
    - Dynamic badge updates
    """

    _template_name = "notifications/notification_bell.html"
    _subscriptions = {"notifications.notification", "notifications-refresh"}

    is_open: bool = False

    @property
    def unread_count(self):
        """Count of unread notifications."""
        return Notification.objects.filter(is_read=False).count()

    async def toggle_dropdown(self):
        """
        Toggle the notification dropdown.

        Demonstrates JS().toggle() with transition effects.
        """
        self.is_open = not self.is_open

        if self.is_open:
            # Show dropdown with animation
            await self.push_js(JS().show(f"#{self.id} .notification-dropdown", transition="fade-in 200ms"))
        else:
            # Hide dropdown
            await self.push_js(JS().hide(f"#{self.id} .notification-dropdown", transition="fade-out 150ms"))

    async def close_dropdown(self):
        """Close the dropdown."""
        if self.is_open:
            self.is_open = False
            await self.push_js(JS().hide(f"#{self.id} .notification-dropdown", transition="fade-out 150ms"))

    async def mutation(self, channel: str, action: ModelAction, instance: Notification):
        """Update badge when notifications change."""
        self.force_render()

    async def notification(self, channel: str, **kwargs):
        """
        Handle custom notifications.

        This is called when broadcast() sends to 'notifications-refresh'.
        """
        if channel == "notifications-refresh":
            self.force_render()


class XNotificationList(Component):
    """
    List of notifications using Streams API.

    Demonstrates:
    - stream() for initial load
    - stream_insert(at=0) for prepending new notifications
    - stream_delete() for removing notifications
    - Model subscriptions for real-time updates
    """

    _template_name = "notifications/notification_list.html"
    _subscriptions = {"notifications.notification"}

    async def joined(self):
        """Load initial notifications using Streams."""
        notifications = list(await Notification.objects.all()[:20])
        await self.stream("notifications", notifications)

    async def mutation(
        self,
        channel: str,
        action: ModelAction,
        instance: Notification,
    ):
        """
        Handle notification changes.

        Demonstrates stream_insert and stream_delete for real-time updates.
        """
        if action == ModelAction.CREATED:
            # Prepend new notification at the top
            await self.stream_insert("notifications", instance, at=0)
            # Add attention animation
            await self.push_js(JS().transition(f"#notifications-{instance.id}", "pulse 500ms"))
        elif action == ModelAction.DELETED:
            # Remove from list
            await self.stream_delete("notifications", instance.id)
        else:
            # For updates, force re-render of the item
            self.force_render()

    async def dismiss(self, notification_id: int):
        """
        Dismiss (delete) a notification.

        Demonstrates stream_delete with animation.
        """
        # Animate out
        await self.push_js(JS().transition(f"#notifications-{notification_id}", "slide-out-right 200ms"))

        # Delete from database
        await Notification.objects.filter(id=notification_id).adelete()

        # Broadcast refresh to update bell badge
        await self.abroadcast("notifications-refresh")

    async def mark_as_read(self, notification_id: int):
        """Mark a notification as read."""
        await Notification.objects.filter(id=notification_id).aupdate(is_read=True)

        # Update styling
        await self.push_js(JS().add_class(f"#notifications-{notification_id}", "is-read"))

        # Broadcast to update badge
        await self.abroadcast("notifications-refresh")

    async def mark_all_read(self):
        """Mark all notifications as read."""
        await Notification.objects.filter(is_read=False).aupdate(is_read=True)

        # Update all items
        await self.push_js(JS().add_class(f"#{self.id} .notification-item", "is-read"))

        # Broadcast refresh
        await self.abroadcast("notifications-refresh")

    async def clear_all(self):
        """Delete all notifications."""
        await Notification.objects.all().adelete()

        # Clear the stream
        await self.stream("notifications", [])

        # Broadcast refresh
        await self.abroadcast("notifications-refresh")


class XNotificationCreator(Component):
    """
    Form to create new notifications (for demo purposes).

    Demonstrates:
    - Form handling
    - Creating notifications that trigger real-time updates
    - push_js() for form reset
    """

    _template_name = "notifications/notification_creator.html"

    title: str = ""
    message: str = ""
    type: str = NotificationType.INFO

    async def set_title(self, title: str):
        """Set title from input."""
        self.title = title
        self.skip_render()

    async def set_message(self, message: str):
        """Set message from input."""
        self.message = message
        self.skip_render()

    async def set_type(self, type: str):
        """Set notification type."""
        self.type = type

    async def create(self):
        """
        Create a new notification.

        The model subscription will automatically update the list.
        """
        if not self.title.strip():
            return

        await Notification.objects.acreate(
            title=self.title.strip(),
            message=self.message.strip(),
            type=self.type,
        )

        # Reset form
        self.title = ""
        self.message = ""
        self.type = NotificationType.INFO

        # Clear inputs using JS
        await self.push_js(
            JS()
            .set_value(f"#{self.id} input[name=title]", "")
            .set_value(f"#{self.id} textarea[name=message]", "")
            .focus(f"#{self.id} input[name=title]")
        )
