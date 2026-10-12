"""The demo's session store: the database one, with a budget for new sessions.

Some examples make a session on a plain GET (chat's room, quiz, rating: the component tells
visitors apart by the session key, and only a page's response can carry the cookie). A client
that never sends the cookie back makes a stored row on every GET. So a new row -- not a new key
for a session that already has one (``cycle_key``, a sign-in) -- costs:

- one of the visitor's ``SESSIONS_PER_HOUR`` (the visitor ``RequestLimitMiddleware`` read), and
- room under ``SESSIONS_MAX``, the rows the database may hold. The janitor deletes expired rows
  and resets the demo when the file grows past ``DB_MAX_MB`` (demo_janitor).

Past either, ``create()`` raises ``SessionRefused`` and the middleware answers 429 or 503.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore as DatabaseStore

from .limits import current_visitor, over_limit


class SessionRefused(Exception):
    def __init__(self, answer: str, status: int):
        super().__init__(answer)
        self.answer = answer
        self.status = status


def admit() -> None:
    """Spend a new session of this request's visitor, or refuse it."""
    budget = settings.DEMO_LIMITS
    visitor = current_visitor.get()
    if visitor is not None and over_limit("session", visitor, budget["SESSIONS_PER_HOUR"], 3600):
        raise SessionRefused("Too many new sessions from here. Let this browser keep its cookie, or wait an hour.", 429)
    if SessionStore.get_model_class().objects.count() >= budget["SESSIONS_MAX"]:
        raise SessionRefused("The demo is full right now. Try again later.", 503)


class SessionStore(DatabaseStore):
    _replacing = False

    def create(self):
        if not self._replacing:
            admit()
        super().create()

    def cycle_key(self):
        # A new key for a session that has one: the old row goes, so nothing is added
        self._replacing = self.session_key is not None
        try:
            super().cycle_key()
        finally:
            self._replacing = False
