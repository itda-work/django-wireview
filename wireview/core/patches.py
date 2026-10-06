"""``Broadcast``: one patch, rendered once, for every subscriber of a topic (#178).

A notification makes every subscribed connection run its receiver, render its
stream item and send it back to itself through the channel layer: a thousand
viewers of a feed rendered the same ``<li>`` a thousand times. A ``Broadcast``
renders the item once, where it is published, and serializes the frames each
connection will write once too. What differs from one connection to the next
is the id of the component the frame is for, so a frame travels as the text
before that id and the text after it (``[head, tail]``), and a connection
writes ``head + json.dumps(id) + tail``: byte for byte the frame its session
would have written for the same ``stream_insert``, ``stream_delete``,
``push_event`` or ``push_js``. The browser protocol does not change.

Only what the server does not track can change this way: stream items, hook
events and JS commands. A field, a render, ``data-state`` cannot -- the next
diff or the next join would undo it (docs/design/broadcast-patch.md §3-1).

Who receives one is the publisher's to say, by class and topic: the instances
of exactly that class whose ``get_subscriptions()`` holds the topic, and that
their connection can reach (``repo.reachable``). Nothing of theirs runs, so
nothing can tell one viewer from another, and an item's template that reads
the viewer raises where it is published: ``this``, ``user``, ``request``,
``perms`` and ``csrf_token`` are ``Watched`` (core/watched.py). It renders in
the project's default language and time zone, not the publisher's.
"""

from __future__ import annotations

import json
import secrets
import typing as t
import weakref

from asgiref.sync import async_to_sync
from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone, translation

from .. import telemetry
from ..utils import db
from .transport import get_broker, require_patch_topic
from .watched import Watched

if t.TYPE_CHECKING:
    from ..js import JS
    from .component import Component

#: The channel-layer message type a ``Broadcast`` publishes. Its handler is
#: ``WireviewSession.wireview_patch``.
MESSAGE_TYPE = "wireview.patch"

#: The names an item's template may not read: what tells one viewer from another.
WATCHED_NAMES = ("this", "user", "request", "perms", "csrf_token")

#: How many frames a component may be sent while it is held -- joined, its
#: ``joined()`` operations not yet written -- before its connection is closed
#: rather than let fall behind (``WireviewSession.wireview_patch``).
HOLD_LIMIT = 1000

#: What a frame holds where the component id goes until it is cut in two.
#: Random per process, so no rendered HTML holds it by chance.
_ID_SLOT = f"wireview-patch-id-{secrets.token_hex(12)}"

#: The receivers ``wireview.testing`` registers: a mounted component hears a
#: Broadcast published in its process as a session would.
_listeners: weakref.WeakSet[t.Any] = weakref.WeakSet()


class BroadcastRenderError(ImproperlyConfigured):
    """A ``Broadcast`` item's template read something of the viewer."""


def encode(command: str, payload: dict[str, t.Any]) -> str:
    """A server-to-client frame as the consumer writes it (``AsyncJsonWebsocketConsumer.encode_json``)."""
    return json.dumps({"command": command, "payload": payload})


def cut(command: str, payload: dict[str, t.Any]) -> list[str]:
    """``[head, tail]``: the frame of ``payload``, whose component id is ``_ID_SLOT``, either side of the id."""
    parts = encode(command, payload).split(json.dumps(_ID_SLOT))
    if len(parts) != 2:
        raise RuntimeError(f"A {command} frame holds the component id other than once")
    return parts


def join(frame: t.Sequence[str], component_id: str) -> str:
    """The frame ``cut`` made, for the component ``component_id``."""
    return frame[0] + json.dumps(component_id) + frame[1]


def listen(receiver: t.Any) -> None:
    """Hand ``receiver._receive_patch(message)`` each Broadcast published in this process (``wireview.testing``)."""
    _listeners.add(receiver)


class Broadcast:
    """Stream items, hook events and JS commands for every subscriber of a topic, rendered once.

    ::

        from wireview import Broadcast

        await Broadcast(Feed, "feed").stream_insert("items", post, at=0, limit=50).asend()

        # A signal receiver, or other sync code: sends once the transaction commits
        Broadcast(Feed, "feed").stream_delete("items", instance.pk).send()

    ``target`` is the component class that receives it, and only that class --
    not a subclass, which may draw its items another way. ``topic`` is one its
    instances return from ``get_subscriptions()``. The operations go out in the
    order they were added, in one message, and take the arguments of the
    component's methods of the same names; ``js`` is ``push_js``.

    The items render once, with ``item`` in the context and nothing else: a
    template that reads ``this``, ``user``, ``request``, ``perms`` or
    ``csrf_token`` raises ``ImproperlyConfigured`` where it is published,
    since every subscriber gets the same HTML. They render in
    ``LANGUAGE_CODE`` and the default time zone.

    Delivery is at most once, as for a notification: a connection that misses
    one -- it was offline, the channel layer dropped it -- is put right by its
    next join, whose ``joined()`` sends its list again.
    """

    def __init__(self, target: type[Component], topic: str) -> None:
        from .component import Component

        if not (isinstance(target, type) and issubclass(target, Component)) or target is Component:
            raise TypeError(f"Broadcast() takes the component class that receives it, not {target!r}")
        require_patch_topic(topic)
        if target.get_subscriptions is Component.get_subscriptions and topic not in target._meta.subscriptions:
            # Its topics are its Meta's, so no instance of it could hear this one:
            # a typo would send every patch nowhere without a word
            raise ValueError(
                f"{target.__qualname__} does not subscribe to {topic!r}: its Meta.subscriptions are "
                f"{sorted(target._meta.subscriptions)}, so no instance would receive this Broadcast"
            )
        self.target = target
        self.topic = topic
        self._ops: list[tuple[t.Any, ...]] = []

    def __repr__(self) -> str:
        return f"Broadcast({self.target.__qualname__}, {self.topic!r}, {len(self._ops)} operation(s))"

    # Operations

    def stream_insert(
        self,
        name: str,
        item: t.Any,
        *,
        at: int = -1,
        limit: int = 0,
        template: str | None = None,
        dom_id: t.Callable[[t.Any], str] | None = None,
    ) -> Broadcast:
        """Insert ``item`` into the stream ``name``, as ``Component.stream_insert``.

        The template defaults to the target class's (``<template_name>_item.html``)
        and the DOM id to ``"<name>-<pk>"``, as the component's own methods do, so
        a ``stream()`` in ``joined()`` and a Broadcast name an item alike.
        """
        self._ops.append(("insert", name, item, at, limit, template, dom_id))
        return self

    def stream_delete(self, name: str, dom_id: str | int) -> Broadcast:
        """Delete an item from the stream ``name`` by DOM id, or by pk (an int), as ``Component.stream_delete``."""
        self._ops.append(("delete", name, f"{name}-{dom_id}" if isinstance(dom_id, int) else dom_id))
        return self

    def push_event(
        self, event: str, payload: dict[str, t.Any] | None = None, *, hook_id: str | None = None
    ) -> Broadcast:
        """Push ``event`` to the target's hooks, as ``Component.push_event``. ``payload`` must be JSON."""
        payload = payload or {}
        try:
            json.dumps(payload)
        except (TypeError, ValueError) as error:
            raise TypeError(f"A Broadcast push_event payload is sent as JSON: {error}") from error
        self._ops.append(("push_event", event, payload, hook_id))
        return self

    def js(self, js: JS) -> Broadcast:
        """Run ``js`` on the target's element, as ``Component.push_js``."""
        self._ops.append(("js", json.loads(js.to_json())))
        return self

    # Sending

    async def asend(self) -> None:
        """Render the items and publish now. Does nothing when no operation was added."""
        if not self._ops:
            return
        rendering = any(op[0] == "insert" for op in self._ops)
        frames = await db(self._frames)() if rendering else self._frames()
        message = {"type": "wireview.patch", "target": self.target._fqn, "topic": self.topic, "frames": frames}
        broker = get_broker()
        with telemetry.span(telemetry.broadcast_published, sender=type(broker), topic=self.topic, kind="patch") as span:
            span.measure(message)
            await broker.publish_patch(self.topic, message)
        for receiver in list(_listeners):
            receiver._receive_patch(message)

    def send(self) -> None:
        """Render the items and publish once the current transaction commits: from sync code.

        As ``broadcast()`` does. The items render after the commit too, from the
        committed rows, and a rolled-back transaction renders nothing.
        """
        from django.db import transaction

        pending = Broadcast(self.target, self.topic)
        pending._ops = list(self._ops)
        transaction.on_commit(lambda: async_to_sync(pending.asend)())

    # Frames

    def _frames(self) -> list[list[str]]:
        """Every operation's frame, cut either side of the component id. Renders the items: off the loop."""
        from ..features.streams import StreamItem, StreamOp

        frames = []
        for op in self._ops:
            kind = op[0]
            if kind == "insert":
                _, name, item, at, limit, template, dom_id = op
                html = self._render(template or self.target._get_stream_item_template(), item)
                item_id = dom_id(item) if dom_id is not None else f"{name}-{item.pk}"
                stream_op = StreamOp(op="insert", stream=name, items=[StreamItem(item_id, html)], at=at, limit=limit)
                frames.append(cut("stream_op", {**stream_op.to_payload(), "id": _ID_SLOT}))
            elif kind == "delete":
                _, name, item_id = op
                stream_op = StreamOp(op="delete", stream=name, items=[StreamItem(item_id, "")])
                frames.append(cut("stream_op", {**stream_op.to_payload(), "id": _ID_SLOT}))
            elif kind == "push_event":
                _, event, payload, hook_id = op
                frames.append(
                    cut(
                        "push_event", {"component_id": _ID_SLOT, "hook_id": hook_id, "event": event, "payload": payload}
                    )
                )
            else:
                frames.append(cut("exec_js", {"id": _ID_SLOT, "commands": op[1]}))
        return frames

    def _render(self, template_name: str, item: t.Any) -> str:
        """``item`` in its template, as every subscriber will see it."""
        template = self.target._get_template(template_name)
        context: dict[str, t.Any] = {name: self._watched(name, template_name) for name in WATCHED_NAMES}
        context["item"] = item
        with translation.override(django_settings.LANGUAGE_CODE), timezone.override(timezone.get_default_timezone()):
            return template.render(context)

    def _watched(self, name: str, template_name: str) -> Watched:
        target = self.target.__qualname__
        return Watched(
            name,
            BroadcastRenderError,
            f"{template_name}, rendered for Broadcast({target}, {self.topic!r}), read {name!r}: the item is "
            f"rendered once and every subscriber gets the same HTML, so it can read nothing of a viewer. "
            f"Read only 'item', or send the item from each component's notification() instead.",
            stands_for=self.target if name == "this" else None,
        )
