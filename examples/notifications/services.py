"""
How a notification reaches one user, in the two shapes this example shows.

A **notification** (pattern A) is a row. It waits in the recipient's list until
they read or dismiss it, and it reaches their open pages by itself: saving it
fires auto-broadcast on the recipient's channel.

A **toast** (pattern B) is not stored. It goes to the recipient's pages that are
open right now and nowhere else; a page opened later never sees it. It is shown
with ``put_flash()``, so a toast is a flash message that someone else sent. A
flash a user causes themselves needs none of this -- the handler calls
``self.put_flash()`` on its own connection.

Every channel here carries the user's primary key, and every component asks for
the channels of ``self.user`` only, so nothing sent to one user reaches another.
"""

from wireview import abroadcast, broadcast

from .models import Notification, NotificationType


def notifications_channel(user) -> str:
    """Where auto-broadcast announces changes to ``user``'s notifications.

    Not a name this module chooses: it is ``{related model}.{pk}.{related_name}``
    for ``Notification.user``.
    """
    return f"auth.user.{user.pk}.notifications"


def refresh_channel(user) -> str:
    """For changes auto-broadcast does not see: ``aupdate()`` sends no signal."""
    return f"notifications-refresh.user.{user.pk}"


def toast_channel(user) -> str:
    return f"toasts.user.{user.pk}"


def notify(user, title: str, message: str = "", type: str = NotificationType.INFO) -> Notification:
    """Store a notification for ``user``. Their open pages learn of it from the save."""
    return Notification.objects.create(user=user, title=title, message=message, type=type)


async def anotify(user, title: str, message: str = "", type: str = NotificationType.INFO) -> Notification:
    return await Notification.objects.acreate(user=user, title=title, message=message, type=type)


def toast(user, message: str, type: str = NotificationType.INFO) -> None:
    """Show ``message`` on ``user``'s open pages, without storing it."""
    broadcast(toast_channel(user), flash_type=type, message=message)


async def atoast(user, message: str, type: str = NotificationType.INFO) -> None:
    await abroadcast(toast_channel(user), flash_type=type, message=message)
