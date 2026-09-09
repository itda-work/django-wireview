"""Read-only access to the Django session from a component (GAP-029, #68).

Phoenix hands the session to ``mount/3``. wireview hands the same thing to
``self.session`` on every component and, unchanged, to the third argument of the
``_on_mount`` hooks.

What arrives there is a view, not Django's ``SessionStore``, for two reasons.

- **It is read-only.** A component lives on a WebSocket, which has no HTTP
  response to carry ``Set-Cookie`` and no point at which Channels flushes a
  modified session. A write that appeared to work would be dropped on the floor,
  so a write raises instead. Sessions are written in a view.
- **It is a snapshot on the socket.** ``SessionStore`` reads its data from the
  session backend on first access, which raises ``SynchronousOnlyOperation``
  inside an async handler. :func:`load_session` materializes the view once, off
  the event loop, before any component sees it. With ``AuthMiddlewareStack`` the
  data is already in memory by then, so the snapshot usually costs nothing.

The HTTP (dead) render keeps the store and materializes on first read: a page
whose components never look at the session pays nothing for this.
"""

from __future__ import annotations

import typing as t
from collections.abc import Mapping

from channels.db import database_sync_to_async

__all__ = ("SessionView", "load_session")

_READ_ONLY = (
    "The session is read-only inside a component. A WebSocket has no response to "
    "carry Set-Cookie and Channels never flushes a session changed here, so the "
    "write would be lost. Write the session in a view instead."
)


class SessionView(Mapping):
    """A read-only mapping over the session a component was mounted from.

    Reads behave like a dict (``self.session["cart_id"]``, ``.get()``, ``in``,
    iteration) and :attr:`session_key` gives the key Django keeps in the cookie,
    which is ``None`` until a view creates the session.
    """

    __slots__ = ("_data", "_store", "_key")

    def __init__(self, data: Mapping | None = None, *, session_key: str | None = None):
        self._data: dict[str, t.Any] | None = None if data is None else dict(data)
        self._store: t.Any = None
        self._key = session_key

    @classmethod
    def wrap(cls, source: t.Any, *, session_key: str | None = None) -> "SessionView":
        """Adapt whatever the call site had into a view.

        A ``SessionView`` passes through, ``None`` becomes an empty view, a
        mapping is copied, and anything else is taken for a Django session store
        and read on first access.
        """
        if isinstance(source, SessionView):
            return source
        if source is None:
            return cls(session_key=session_key)
        if isinstance(source, Mapping):
            return cls(source, session_key=session_key)
        view = cls(session_key=session_key)
        view._store = source
        return view

    def _snapshot(self) -> dict[str, t.Any]:
        """Materialize the store, which is where the backend read happens."""
        if self._data is None:
            self._data = dict(self._store.items()) if self._store is not None else {}
        return self._data

    @property
    def session_key(self) -> str | None:
        """The session key from the cookie, or ``None`` when there is no session."""
        if self._store is not None:
            return self._store.session_key
        return self._key

    @property
    def loaded(self) -> bool:
        """Whether the session data has been read yet."""
        return self._data is not None

    # Mapping protocol

    def __getitem__(self, key: str) -> t.Any:
        return self._snapshot()[key]

    def __iter__(self) -> t.Iterator[str]:
        return iter(self._snapshot())

    def __len__(self) -> int:
        return len(self._snapshot())

    def __eq__(self, other: t.Any) -> bool:
        if isinstance(other, SessionView):
            return self._snapshot() == other._snapshot()
        if isinstance(other, Mapping):
            return self._snapshot() == dict(other)
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        key = f"key={self.session_key!r}"
        if self._data is None:
            return f"<SessionView {key} unread>"
        # Keys, never values: a session repr lands in logs and tracebacks.
        return f"<SessionView {key} fields={sorted(self._data)}>"

    # Writes

    def __setitem__(self, key: str, value: t.Any) -> t.NoReturn:
        raise TypeError(_READ_ONLY)

    def __delitem__(self, key: str) -> t.NoReturn:
        raise TypeError(_READ_ONLY)

    def update(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise TypeError(_READ_ONLY)

    def setdefault(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise TypeError(_READ_ONLY)

    def pop(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise TypeError(_READ_ONLY)

    def clear(self) -> t.NoReturn:
        raise TypeError(_READ_ONLY)

    def flush(self) -> t.NoReturn:
        raise TypeError(_READ_ONLY)

    def save(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise TypeError(_READ_ONLY)


async def load_session(source: t.Any) -> SessionView:
    """Return a view whose data is already in memory.

    The WebSocket path calls this once per connection so that later reads are
    dict lookups on the event loop rather than a session-backend query.
    """
    view = SessionView.wrap(source)
    if not view.loaded:
        await database_sync_to_async(view._snapshot)()
    return view
