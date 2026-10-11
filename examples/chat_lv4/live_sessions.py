"""
The example's two boundaries: the room page's, and the room list's. Autodiscovered like ``live.py``.

For the room page, ``authorize`` runs before the room view renders and again before a join mounts
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

#: The room list's page. No policy: anyone may see it, a visitor signs in there.
#: It is a boundary so that a logout reaches it. A connection outside every
#: boundary does not hear logouts, and keeps the user it connected with: the
#: list would go on hearing, and showing, the badges of someone who signed out.
#: (Its Join and Leave are forms: each POST is the request's user.) Inside one,
#: the logout closes the socket, and the page comes back as the visitor.
chat_lv4_rooms = live_session("chat_lv4_rooms")
