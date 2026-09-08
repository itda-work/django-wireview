"""Opt-in telemetry hooks for the component lifecycle (GAP-022).

wireview emits four Django signals while it works. Connect to them to feed
your own metrics backend; wireview itself never records anything.

===========================  ==================================================
Signal                       Fired when
===========================  ==================================================
``event_handled``            a client event handler finished
``component_rendered``       a template render finished
``diff_computed``            a diff was computed from the rendered HTML
``broadcast_published``      a message was published to a fan-out topic
===========================  ==================================================

Every signal carries ``duration_ms`` (float), ``payload_size`` (bytes, or
``None`` when the size is not measurable) and ``error`` (the exception that
escaped the measured block, or ``None``). The per-signal keyword arguments are
documented in ``docs/features/telemetry.md``.

Telemetry is off unless ``WIREVIEW["TELEMETRY"]`` is true. While it is off the
instrumented code paths take a shared no-op span, so no signal is sent, no
payload is measured and no clock is read. Use :func:`enable` / :func:`disable`
to toggle it at runtime (tests do).
"""

from __future__ import annotations

import json
import time
import typing as t

from django.dispatch import Signal

from . import settings

__all__ = (
    "event_handled",
    "component_rendered",
    "diff_computed",
    "broadcast_published",
    "is_enabled",
    "enable",
    "disable",
    "span",
    "payload_size",
)


#: A client event handler finished.
#: sender: component class. Kwargs: ``component_id``, ``component_name``,
#: ``event``, ``duration_ms``, ``payload_size``, ``error``.
event_handled = Signal()

#: A template render finished.
#: sender: component class. Kwargs: ``component_id``, ``component_name``,
#: ``live``, ``duration_ms``, ``payload_size``, ``error``.
component_rendered = Signal()

#: A diff was computed from the rendered HTML.
#: sender: component class. Kwargs: ``component_id``, ``component_name``,
#: ``changed``, ``duration_ms``, ``payload_size``, ``error``.
diff_computed = Signal()

#: A message was published to a fan-out topic.
#: sender: broker class. Kwargs: ``topic``, ``duration_ms``, ``payload_size``,
#: ``error``.
broadcast_published = Signal()


_enabled: bool = settings.TELEMETRY


def is_enabled() -> bool:
    """Whether telemetry spans are being measured."""
    return _enabled


def enable() -> None:
    """Start measuring telemetry spans."""
    global _enabled
    _enabled = True


def disable() -> None:
    """Stop measuring telemetry spans."""
    global _enabled
    _enabled = False


def payload_size(value: t.Any) -> int | None:
    """Size of ``value`` in bytes as it would go over the wire.

    Returns ``None`` for values that cannot be sized (unserializable objects).
    """
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return None


class Span:
    """A measured block. Sends its signal once, on exit."""

    __slots__ = ("_signal", "_info", "_size", "_start")

    enabled = True

    def __init__(self, signal: Signal, info: dict[str, t.Any]) -> None:
        self._signal = signal
        self._info = info
        self._size: int | None = None
        self._start = 0.0

    def measure(self, value: t.Any) -> None:
        """Record the payload size of ``value`` for this span."""
        self._size = payload_size(value)

    def annotate(self, **info: t.Any) -> None:
        """Add keyword arguments to the signal this span will send."""
        self._info.update(info)

    def __enter__(self) -> "Span":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: t.Any, exc: BaseException | None, tb: t.Any) -> bool:
        duration_ms = (time.perf_counter() - self._start) * 1000
        self._signal.send(
            duration_ms=duration_ms,
            payload_size=self._size,
            error=exc,
            **self._info,
        )
        return False


class _NullSpan:
    """The span used while telemetry is off. Measures nothing, sends nothing."""

    __slots__ = ()

    enabled = False

    def measure(self, value: t.Any) -> None:
        return None

    def annotate(self, **info: t.Any) -> None:
        return None

    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, exc_type: t.Any, exc: BaseException | None, tb: t.Any) -> bool:
        return False


_NULL_SPAN = _NullSpan()


def span(signal: Signal, sender: t.Any = None, **info: t.Any) -> "Span | _NullSpan":
    """Measure a block and send ``signal`` when it exits.

    While telemetry is off this returns a shared no-op span, so callers pay
    only the cost of the ``with`` statement.

    Example:
        with telemetry.span(telemetry.diff_computed, sender=type(component)) as s:
            diff = compute()
            s.measure(diff)
    """
    if not _enabled:
        return _NULL_SPAN
    info["sender"] = sender
    return Span(signal, info)
