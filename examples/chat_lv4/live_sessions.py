"""
The room page's boundary. Autodiscovered like ``live.py``.

``authorize`` runs before the room view renders and again before a join mounts
anything; the hook runs for every component the page draws or joins. Together
they say "signed in, and a member of this component's room" (``access.py``).
"""

from wireview import live_session

from .access import RoomMembersOnly, signed_in

#: An anonymous visitor is sent to the room list, which is where the demo signs in.
#: The name is resolved per request (Django's resolve_url), so it is safe at import.
chat_lv4 = live_session(
    "chat_lv4",
    authorize=signed_in,
    on_mount=[RoomMembersOnly],
    login_url="chat_lv4:index",
)
