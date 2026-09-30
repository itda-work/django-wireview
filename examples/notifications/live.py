"""
Notifications App Components

This module demonstrates wireview's advanced communication patterns:
- Per-user channels from get_subscriptions() and self.user
- mutation() for stored notifications, atoast() for toasts ({% wireview_toasts %} shows them)
- broadcast() / abroadcast() for sending messages
- push_js() with JS() command builder
- Streams API for notification list
- JS().show(), JS().hide(), JS().toggle(), JS().transition()
"""

from django.contrib.auth import get_user_model

from wireview import JS, Component, ModelAction, atoast

from .models import Notification, NotificationType
from .services import anotify, notifications_channel, refresh_channel


class XNotificationBell(Component):
    """
    Notification bell icon with unread count badge.

    Demonstrates:
    - get_subscriptions() naming the signed-in user's channels
    - notification() hook for custom events
    - push_js() with JS() commands
    - Dynamic badge updates
    """

    class Meta:
        template_name = "notifications/notification_bell.html"

    is_open: bool = False

    def get_subscriptions(self) -> set[str]:
        # Only this user's channels. An anonymous visitor has none.
        if not self.user.is_authenticated:
            return set()
        return {notifications_channel(self.user), refresh_channel(self.user)}

    @property
    def unread_count(self):
        """Count of this user's unread notifications."""
        if not self.user.is_authenticated:
            return 0
        return Notification.objects.filter(user=self.user, is_read=False).count()

    async def toggle_dropdown(self):
        """
        Toggle the notification dropdown.

        Demonstrates JS().toggle() with transition effects.
        """
        self.is_open = not self.is_open

        if self.is_open:
            # Show dropdown with animation
            await self.push_js(JS().show(f"#{self.id} .notification-dropdown", transition=("fade-in", 200)))
        else:
            # Hide dropdown
            await self.push_js(JS().hide(f"#{self.id} .notification-dropdown", transition=("fade-out", 150)))

    async def close_dropdown(self):
        """Close the dropdown."""
        if self.is_open:
            self.is_open = False
            await self.push_js(JS().hide(f"#{self.id} .notification-dropdown", transition=("fade-out", 150)))

    async def mutation(self, channel: str, action: ModelAction, instance: Notification):
        """Update badge when notifications change."""
        self.force_render()

    async def notification(self, channel: str, **kwargs):
        """The refresh channel is for changes auto-broadcast does not see."""
        if channel == refresh_channel(self.user):
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

    class Meta:
        template_name = "notifications/notification_list.html"

    def get_subscriptions(self) -> set[str]:
        if not self.user.is_authenticated:
            return set()
        return {notifications_channel(self.user)}

    def _mine(self):
        """This user's notifications. Every handler starts here.

        The ids a handler receives come from the browser, and a browser can send
        any id; filtering by the owner is what keeps one user from reading or
        deleting another's notifications.
        """
        if not self.user.is_authenticated:
            return Notification.objects.none()
        return Notification.objects.filter(user=self.user)

    async def joined(self):
        """Load initial notifications using Streams."""
        notifications = [n async for n in self._mine()[:20]]
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
            await self.push_js(JS().transition(f"#notifications-{instance.id}", ("pulse", 500)))
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
        await self.push_js(JS().transition(f"#notifications-{notification_id}", ("slide-out-right", 200)))

        # Delete from database. The delete is announced on the user's channel,
        # which updates the bell as well.
        await self._mine().filter(id=notification_id).adelete()

    async def mark_as_read(self, notification_id: int):
        """Mark a notification as read."""
        if not await self._mine().filter(id=notification_id).aupdate(is_read=True):
            return

        # Update styling
        await self.push_js(JS().add_class(f"#notifications-{notification_id}", "is-read"))

        # aupdate() sends no signal, so the bell has to be told
        await self.broadcast(refresh_channel(self.user))

    async def mark_all_read(self):
        """Mark all notifications as read."""
        if not await self._mine().filter(is_read=False).aupdate(is_read=True):
            return

        # Update all items
        await self.push_js(JS().add_class(f"#{self.id} .notification-item", "is-read"))

        # aupdate() sends no signal, so the bell has to be told
        await self.broadcast(refresh_channel(self.user))

    async def clear_all(self):
        """Delete all notifications."""
        await self._mine().adelete()

        # Clear the stream
        await self.stream("notifications", [])


class XNotificationCreator(Component):
    """
    Form that sends a notification or a toast to any user (for demo purposes).

    Demonstrates:
    - Form handling
    - Creating notifications that trigger real-time updates on the recipient's pages
    - Sending a toast that is shown and never stored
    - push_js() for form reset
    """

    class Meta:
        template_name = "notifications/notification_creator.html"

    recipient: str = ""
    title: str = ""
    message: str = ""
    type: str = NotificationType.INFO

    @property
    def usernames(self) -> list[str]:
        """Who can be sent to: everyone, so two signed-in windows can talk."""
        return list(get_user_model().objects.order_by("username").values_list("username", flat=True))

    async def _recipient(self):
        """The chosen user, or the sender when none is chosen."""
        if not self.recipient:
            return self.user if self.user.is_authenticated else None
        return await get_user_model().objects.filter(username=self.recipient).afirst()

    async def set_recipient(self, recipient: str):
        self.recipient = recipient

    @property
    def can_send(self) -> bool:
        return bool(self.title.strip())

    async def set_title(self, title: str):
        """Set title from input.

        The input already shows what was typed, so most keystrokes need no
        render -- but the send buttons are disabled until there is a title, and
        a skipped render would leave them disabled.
        """
        could_send = self.can_send
        self.title = title
        if self.can_send == could_send:
            self.skip_render()

    async def set_message(self, message: str):
        """Set message from input."""
        self.message = message
        self.skip_render()

    async def set_type(self, type: str):
        """Set notification type."""
        if type in NotificationType.values:
            self.type = type

    async def create(self):
        """
        Create a new notification.

        The model subscription will automatically update the list.
        """
        if not self.can_send:
            return
        recipient = await self._recipient()
        if recipient is None:
            await self.put_flash("error", "Choose who gets it.")
            return

        await anotify(recipient, self.title.strip(), self.message.strip(), self.type)

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

    async def send_toast(self):
        """
        Show the title to the recipient right now, without storing anything.

        Nothing is written, so auto-broadcast has nothing to announce; the toast
        goes on the recipient's channel directly. The sender's own form is
        unchanged, which is why this render is skipped.
        """
        if not self.can_send:
            return
        recipient = await self._recipient()
        if recipient is None:
            await self.put_flash("error", "Choose who gets it.")
            return

        await atoast(recipient, self.title.strip(), flash_type=self.type)
        self.skip_render()
