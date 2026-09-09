"""A session backend whose store cannot be read. Used by one test.

``_reload_session`` has to fail closed when the session backend is unreachable,
and the only way to test the ``except`` branch is to make the read raise where
the code actually performs it. Mocking the method that contains the branch tests
the mock.
"""

from django.contrib.sessions.backends.base import SessionBase


class SessionStore(SessionBase):
    """Raises on every read. Never used outside ``override_settings``."""

    def load(self):
        raise RuntimeError("the session backend is unreachable")

    def exists(self, session_key):
        raise RuntimeError("the session backend is unreachable")

    def create(self):
        raise RuntimeError("the session backend is unreachable")

    def save(self, must_create=False):
        raise RuntimeError("the session backend is unreachable")

    def delete(self, session_key=None):
        raise RuntimeError("the session backend is unreachable")
