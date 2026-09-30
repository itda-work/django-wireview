"""Toasts: a flash message sent to someone else's open pages (#116).

A flash (``put_flash``) goes to the connection whose event caused it. A toast
comes from elsewhere -- another user's action, a view, a background job -- and
goes to every page its recipient has open. The only thing that adds is the
route, so this module is a channel name, the two functions that send on it, and
the component that listens on it. Drawing stays ``put_flash()``'s, so a toast
and a flash look the same.

The recipient is a user, or a session key for a visitor who has not signed in.
The channel carries that value and the receiver subscribes only to its own
connection's user and session, which is the whole access control: a toast for
one user cannot reach another's pages.
"""

from __future__ import annotations

import typing as t

from ..core.component import Component, abroadcast, broadcast

__all__ = ["WireviewToasts", "atoast", "toast", "toast_channel"]

#: What ``put_flash()`` needs, sent as the notification's kwargs.
_FIELDS = ("flash_type", "message", "timeout", "dismissible")


def toast_channel(to: t.Any) -> str:
    """The channel a toast for ``to`` travels on: a saved user, or a session key."""
    if isinstance(to, str):
        if not to:
            raise ValueError("a toast needs a session key; this visitor has no session yet")
        return f"wireview.toast.session.{to}"
    pk = getattr(to, "pk", None)
    if pk is None:
        raise ValueError(f"a toast goes to a saved user or a session key, not {to!r}")
    return f"wireview.toast.user.{pk}"


def _payload(message: str, flash_type: str, timeout: int, dismissible: bool) -> dict[str, t.Any]:
    return {"flash_type": flash_type, "message": message, "timeout": timeout, "dismissible": dismissible}


def toast(to: t.Any, message: str, *, flash_type: str = "info", timeout: int = 5000, dismissible: bool = True) -> None:
    """Show ``message`` on the pages ``to`` has open. Sent after the current transaction commits."""
    broadcast(toast_channel(to), **_payload(message, flash_type, timeout, dismissible))


async def atoast(
    to: t.Any, message: str, *, flash_type: str = "info", timeout: int = 5000, dismissible: bool = True
) -> None:
    """``toast()`` for async code: sent at once."""
    await abroadcast(toast_channel(to), **_payload(message, flash_type, timeout, dismissible))


class WireviewToasts(Component):
    """What ``{% wireview_toasts %}`` puts on a page: it listens and shows, and renders nothing."""

    class Meta:
        template_name = "wireview/toasts.html"

    def get_subscriptions(self) -> set[str]:
        channels = set()
        if self.user.is_authenticated:
            channels.add(toast_channel(self.user))
        if self.session.session_key:
            channels.add(toast_channel(self.session.session_key))
        return channels

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        # Nothing on the page changed: the message goes into [wire-flash], outside.
        self.skip_render()
        if channel not in self.get_subscriptions():
            return
        flash = {key: kwargs[key] for key in _FIELDS if key in kwargs}
        await self.put_flash(flash.pop("flash_type"), flash.pop("message"), **flash)
