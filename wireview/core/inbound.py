"""What one connection may send this server: how big a message, how many a second (#230).

Both are judged on the frame as it arrives, before it is parsed -- a JSON parse
of a 16 MB string is the cost being bounded -- and both end the connection
rather than drop the message. A message dropped leaves the page waiting on an
answer that never comes; a closed socket makes it reconnect, join again from the
state its elements carry, and back off (``static/wireview/reconnect.mjs``).

=====================  ======  ==========================================================
Refusal                Code    When
=====================  ======  ==========================================================
``size``               1009    a frame is larger than ``INBOUND_MAX_BYTES`` (UTF-8 bytes)
``rate``               1008    more frames than ``INBOUND_BURST`` came at once, or more
                               than ``INBOUND_RATE_PER_SECOND`` a second for long
``binary``             1003    a binary frame: the client only sends text
``components``         1008    a join would hold more than ``CONNECTION_MAX_COMPONENTS``
=====================  ======  ==========================================================

The ASGI server bounds a frame first, while reading it (uvicorn's
``--ws-max-size``, 16 MiB by default): that is what keeps the memory a frame
takes. This bounds what the application then spends on it, alike on every server.
"""

from __future__ import annotations

import time
import typing as t

from .. import settings

#: The close code for each refusal (RFC 6455 section 7.4.1)
CLOSE_CODES: dict[str, int] = {"size": 1009, "rate": 1008, "binary": 1003, "components": 1008}

#: The clock the buckets read. A test moves its own instead of waiting on the wall clock.
monotonic: t.Callable[[], float] = time.monotonic


def frame_size(text: str | None, data: bytes | None) -> int:
    """The frame's size in bytes: a text frame's as UTF-8, as it came on the wire."""
    if text is None:
        return len(data or b"")
    return len(text.encode("utf-8"))


def too_big(text: str | None, data: bytes | None, limit: int | None) -> int | None:
    """The frame's size when it is over ``limit`` bytes, else ``None``. ``None`` is no limit.

    A text frame of ``n`` characters is ``n`` to ``4 n`` bytes in UTF-8: only one
    in between is encoded to tell.
    """
    if limit is None:
        return None
    if text is not None and len(text) * 4 <= limit:
        return None
    size = frame_size(text, data)
    return size if size > limit else None


class Bucket:
    """A token bucket: ``burst`` at once, refilled at ``rate`` a second.

    Starts full, so a page's first round -- a join for each component on it --
    takes from what a burst allows, not from the rate.
    """

    __slots__ = ("burst", "rate", "tokens", "at")

    def __init__(self, burst: int, rate: float) -> None:
        self.burst = float(burst)
        self.rate = float(rate)
        self.tokens = float(burst)
        self.at = monotonic()

    def take(self) -> bool:
        """Take one token; False when there was none left."""
        now = monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Gate:
    """One connection's inbound limits. Settings are read when used: the rate when the first frame comes."""

    __slots__ = ("_bucket", "_read")

    def __init__(self) -> None:
        self._bucket: Bucket | None = None
        self._read = False

    def refusal(self, text: str | None, data: bytes | None) -> tuple[str, int, int | float | None] | None:
        """Why this frame is refused -- ``(reason, size, limit)`` -- or ``None`` to handle it.

        Every frame counts against the rate, whatever it says: a join costs more
        than an event, and a frame the session drops was still read.
        """
        limit = settings.INBOUND_MAX_BYTES
        if (size := too_big(text, data, limit)) is not None:
            return "size", size, limit
        if not self._read:
            self._read = True
            per_second = settings.INBOUND_RATE_PER_SECOND
            if per_second is not None:
                self._bucket = Bucket(settings.INBOUND_BURST, per_second)
        if self._bucket is not None and not self._bucket.take():
            return "rate", frame_size(text, data), self._bucket.rate
        if text is None:
            return "binary", frame_size(text, data), None
        return None
