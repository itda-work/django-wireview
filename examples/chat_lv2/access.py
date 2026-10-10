"""
Who may be in a room, decided in one place.

``is_member`` is the whole rule. Three places ask it, and they ask this
function rather than writing the question again:

- the room view, before the page produces a byte (``views.room``);
- every component on the room page, when it mounts -- the HTTP render and
  every WebSocket join, including a join that never went through the view
  (``RoomMembersOnly``, a hook of the ``chat_lv2`` live_session);
- the handler that writes a message (``Lv2ChatRoom.send_message``), because
  a mounted component's events are not authorized again.

The live_session's ``authorize`` cannot ask it. Its context holds the user and
the session and nothing about the page, so it does not know which room. It
answers the part it can -- "signed in" -- and the room is read from each
component's own signed state, which the hook sees and the browser cannot
change.
"""

from asgiref.sync import sync_to_async
from django.urls import reverse

from .models import Membership, Room


def is_member(user, room: Room) -> bool:
    """Whether ``user`` may read and write in ``room``. Anonymous users never may."""
    if not getattr(user, "is_authenticated", False):
        return False
    return Membership.objects.filter(room=room, user_id=user.pk).exists()


def signed_in(context) -> bool:
    """The live_session's ``authorize``: everything it can know is who is asking."""
    return context.user.is_authenticated


class RoomMembersOnly:
    """Refuse a component of the room page unless its user is a member of its room.

    The room comes from the component (``component.room``), which is restored
    from its signed state -- not from the URL or anything else the browser says.
    A component on this page without a room is refused too: there is nothing
    to authorize it against.
    """

    @staticmethod
    async def on_mount(component, params, session):
        room = getattr(component, "room", None)
        if room is not None and await sync_to_async(is_member)(component.user, room):
            return {"cont": True}
        await component.wire.redirect_to(reverse("chat_lv2:index"))
        return {"halt": True}
