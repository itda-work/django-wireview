"""An ``Outbound`` that records what a ``WireviewSession`` sends, for tests that drive one without a socket.

It lives here rather than in a test module: a test that imports another test
module imports it a second time under ``tests.<name>`` (``pythonpath`` also
holds ``tests``), and the components that module defines register twice and
shadow each other by name (``tests/test_suite_imports.py``).
"""

from __future__ import annotations

import typing as t


class RecordingOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []
        self.topics: set[str] = set()
        self.closed: list[int | None] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        self.topics.add(topic)

    async def unsubscribe(self, topic: str) -> None:
        self.topics.discard(topic)

    async def close(self, code: int | None = None) -> None:
        self.closed.append(code)

    def renders(self) -> list[dict[str, t.Any]]:
        return [payload for command, payload in self.commands if command == "render"]
