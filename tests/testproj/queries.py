"""Counting queries from an async test.

``CaptureQueriesContext`` opens the database connection when it is entered, and
on the event loop Django refuses that. The async ORM and wireview's own
``database_sync_to_async`` calls all run on one thread-sensitive worker thread,
with that thread's connection, so that is where the capture has to be: entered
and left there, it counts exactly the queries the code under test ran. Its
``captured_queries`` reads the connection of whichever thread asks, so the list
is copied out on that thread too.
"""

from __future__ import annotations

import contextlib
import typing as t

from asgiref.sync import sync_to_async
from django.db import connection
from django.test.utils import CaptureQueriesContext


class CapturedQueries:
    """What ``capture_queries()`` saw: ``len()`` and ``captured_queries`` as on ``CaptureQueriesContext``."""

    def __init__(self) -> None:
        self.captured_queries: list[dict[str, t.Any]] = []

    def __len__(self) -> int:
        return len(self.captured_queries)


@contextlib.asynccontextmanager
async def capture_queries() -> t.AsyncIterator[CapturedQueries]:
    capture = CaptureQueriesContext(connection)
    result = CapturedQueries()

    def leave() -> None:
        capture.__exit__(None, None, None)
        result.captured_queries = list(capture.captured_queries)

    await sync_to_async(capture.__enter__)()
    try:
        yield result
    finally:
        await sync_to_async(leave)()
