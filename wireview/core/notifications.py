"""A notification or a model mutation, handed to the components that hear it (#201).

The rules are a connection's: each component subscribed to the channel, in
order, runs its receiver -- ``notification()`` or ``mutation()`` -- and is
rendered; one that raises is recovered alone; then the connection settles its
subscriptions again. A joined presence producer hears its presence channel for
the framework alone (``presence.hear``, #202): its receiver does not run and
nothing renders, unless it subscribes to that channel itself.
``WireviewSession`` follows these rules for what the channel layer brings it,
and ``wireview.testing`` for a mounted component, which hears what is
published in its process (``listen``): one copy, so a test sees what a page sees.

A component hears from its ``joined()`` on (#207). One its drawer's pass built
(``{% component %}``) is on the connection before the page's join for it comes:
a round trip later, or much later -- a hidden tab applies no render, and so
joins nothing, until it is shown again. What it would have heard meanwhile
waits on the instance and is handed to it, in arrival order, once it joined
(``deliver_unheard``). Not dropped: the join restores the state the page holds,
the one drawn before it. The wait is bounded by how much waits, not by how
long (``UNHEARD_LIMIT``): a page may never join it -- drawn under
``wire-update="ignore"``, say -- and then the connection retires it.
"""

from __future__ import annotations

import itertools
import typing as t
import weakref

from ..debug import render_queries
from ..features import presence
from . import shared_render

if t.TYPE_CHECKING:
    from .component import Component

#: The fan-out messages a component receives, and the method each one runs.
RECEIVERS = {"notification": "notification", "model_mutation": "mutation"}

#: How many notifications may wait on a component the page has yet to join
#: (``WireviewMeta.unheard``). Past this it is taken for one the page never
#: joins, and retired with what it owns: no ``leaving()``, its tasks cancelled.
#: A join that comes for it after all builds it anew from the state the page
#: holds. No time limit: a hidden tab joins when it is shown again, hours on.
UNHEARD_LIMIT = 1000

#: The order notifications arrived in, across components: a join hands its
#: root's and its LiveComponents' over as they came (``deliver_unheard``)
_arrival = itertools.count()


class Host(t.Protocol):
    """What delivers a notification: a session, or a mounted component in a test."""

    def _subscribed_to(self, channel: str) -> t.Iterable[Component]:
        """The components that hear ``channel``, in the order they receive it: lazily,
        each one asked when its turn comes. A joined presence producer hears its
        presence channel too (``presence.presence_channels``), for the framework."""
        ...

    def _holds(self, component: Component) -> bool:
        """Whether ``component`` is still there: an ancestor that raised earlier in the loop takes it."""
        ...

    async def send_render(self, component: Component) -> None: ...

    async def _crashed(self, component: Component) -> None:
        """Called from the ``except`` block of the receiver or the render that raised."""
        ...

    async def after_mutation_chores(self) -> None: ...

    async def _awaits_join(self, component: Component) -> None:
        """``component`` has yet to join and something waits on it (``wire.unheard``): not without bound."""
        ...


def _arguments(receiver: str, data: dict[str, t.Any]) -> t.Callable[[], dict[str, t.Any]]:
    """The receiver's keyword arguments. Made afresh for each component: one that edits
    what it heard must not change what the next one hears."""
    if receiver == "mutation":
        from .. import serializer

        # The signature here is coupled to `wireview.auto_broadcast.notify_mutation`
        return lambda: {"instance": serializer.decode(data["instance"]), "action": data["action"]}
    # ...and here to `wireview.utils.send_notification`
    return lambda: data["kwargs"]


async def deliver(host: Host, receiver: str, data: dict[str, t.Any]) -> None:
    """Hand ``data`` -- the channel-layer message -- to ``receiver`` of each component of ``host`` that hears it."""
    channel = data["channel"]
    arguments = _arguments(receiver, data)
    message_id = data.get("message_id")
    # Asked of each component in its turn, not all at the start: one's receiver or
    # render can move another off the channel -- a parent's render handing its
    # LiveComponent another room -- and then it no longer hears this
    for component in host._subscribed_to(channel):
        if not host._holds(component):
            continue
        kwargs = arguments()
        try:
            if receiver == "notification":
                await presence.hear(component, channel, kwargs)
            if channel not in component.get_subscriptions():
                continue  # only the framework listens here (#202): no app code, no render
            if not component.wire.has_joined or component.wire.unheard:
                # Not yet (#207): it hears this once it joined -- and once what
                # came before it is handed over, which a join mails behind it
                component.wire.unheard.append((next(_arrival), receiver, channel, kwargs))
                if not component.wire.has_joined:
                    await host._awaits_join(component)
                continue
            await _hand(host, component, receiver, channel, kwargs, message_id)
        except Exception:
            await host._crashed(component)
    await host.after_mutation_chores()


async def deliver_unheard(host: Host, components: t.Iterable[Component]) -> None:
    """Hand ``components``, which just joined, what they would have heard before, as ``deliver`` would.

    A join's root and the LiveComponents its render ran ``joined()`` for, in
    the order it arrived: the root's first and a LiveComponent's second of one
    message, then the next message's. Each still has to be on a channel it
    subscribes to -- ``joined()`` may have chosen others -- and one that raises
    hears no more of its own, nor do those it owns, which go with it.
    """
    waiting = []
    for component in components:
        unheard, component.wire.unheard = component.wire.unheard, []
        waiting += [(arrival, component, rest) for arrival, *rest in unheard]
    waiting.sort(key=lambda each: each[0])
    for _, component, (receiver, channel, kwargs) in waiting:
        if not host._holds(component) or channel not in component.get_subscriptions():
            continue
        try:
            # Not shared with another session's render (#176): that one is of
            # the message as it came, this one of the component as it joined
            await _hand(host, component, receiver, channel, kwargs, None)
        except Exception:
            await host._crashed(component)
    await host.after_mutation_chores()


async def _hand(
    host: Host, component: Component, receiver: str, channel: str, kwargs: dict[str, t.Any], message_id: t.Any
) -> None:
    """Run ``component``'s receiver and render it."""
    with render_queries.scope("handler", component, f"{receiver}({channel!r})"):
        await getattr(component, receiver)(channel, **kwargs)
    # The sessions of this process handling the same message render a
    # Meta.shared_render component once between them (#176). Only the
    # render: each one's receiver above ran on its own.
    with shared_render.handling(message_id), render_queries.reason(f"{receiver} {channel}"):
        await host.send_render(component)


# -- in this process: the components wireview.testing mounted ------------------------------

#: Registered by ``wireview.testing``, in the order they were mounted: a session
#: hears a notification through its channel layer, a mounted component from here.
_listeners: weakref.WeakValueDictionary[int, t.Any] = weakref.WeakValueDictionary()
_order = itertools.count()


def listen(receiver: t.Any) -> None:
    """Hand ``receiver._receive_notification(message)`` each notification published in this process."""
    _listeners[next(_order)] = receiver


def forget(receiver: t.Any) -> None:
    """Hand ``receiver`` nothing more."""
    for key, listener in list(_listeners.items()):
        if listener is receiver:
            del _listeners[key]


async def published(message: dict[str, t.Any]) -> None:
    """``message`` went to the channel layer: the listeners hear it too, before this returns."""
    if message.get("type") not in RECEIVERS or not _listeners:
        return
    for receiver in list(_listeners.values()):
        await receiver._receive_notification(message)
