"""Keep a thread's database connections open while a synchronous render crosses into async code (#190).

Imports nothing of wireview: ``core/meta.py`` needs it while ``wireview.utils``
is still importing ``wireview.core``.
"""

import typing as t
from contextlib import contextmanager

from django.db import connections

__all__ = ("keep_connections",)


def _keep_open() -> None:
    """Stands in for ``close_if_unusable_or_obsolete`` while ``keep_connections`` holds."""


@contextmanager
def keep_connections() -> t.Iterator[None]:
    """Keep this thread's database connections open, against ``close_old_connections``.

    ``async_to_sync`` on a thread that holds a request brings thread-sensitive
    work back to that thread, and Channels' ``database_sync_to_async`` closes the
    thread's connections around it -- hygiene for a worker, but here it is the
    request's connection, and inside ``transaction.atomic()`` Django closes it
    whatever ``CONN_MAX_AGE`` says: the request's writes roll back without an
    exception (#190). Every bridge a synchronous render crosses into component
    code holds this around it.

    Connections are per thread, so only this thread's are held; one held already,
    by a bridge further out, stays held when this one lets go.
    """
    held = [conn for conn in connections.all() if "close_if_unusable_or_obsolete" not in vars(conn)]
    for conn in held:
        conn.close_if_unusable_or_obsolete = _keep_open
    try:
        yield
    finally:
        for conn in held:
            del conn.close_if_unusable_or_obsolete
