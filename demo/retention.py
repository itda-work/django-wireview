"""What the demo keeps of what visitors write, without touching the examples.

The examples keep everything: a chat room's every line, every notification. On a public site
that is a store anyone can fill, so the demo bounds it with model signals (``connect()``, from
``DemoConfig.ready``):

- a table in ``KEPT`` with a group keeps the newest ``keep`` rows of each group (a room's
  messages, a person's notifications) and deletes the older ones as each new row is saved;
- every table in ``KEPT`` holds ``DEMO_ROWS_MAX`` rows at most: a new row past it is refused
  with ``StorageFull`` -- 503 in plain text over HTTP (``RequestLimitMiddleware``); over the
  WebSocket the component joins again from its state before the event (docs/features/errors.md),
  so what was typed stays in its box.

The janitor trims every table again each sweep (``trim_all``), for rows that reached the
database without a signal. The full reset when the file outgrows ``DEMO_DB_MAX_MB`` is the
disk's last guard, not how the demo keeps its size (demo_janitor).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.apps import apps
from django.conf import settings
from django.db import models
from django.db.models.signals import post_save, pre_save

log = logging.getLogger("demo.retention")


class StorageFull(Exception):
    answer = "The demo holds as much of this as it can. It goes back to its seed every day; try again then."


@dataclass(frozen=True)
class Kept:
    """A table visitors write to: the newest ``keep`` rows per ``group`` stay (all of them without one)."""

    model: str
    group: str | None = None
    keep: int | None = None
    newest: tuple[str, ...] = ("-pk",)

    @property
    def model_class(self) -> type[models.Model]:
        return apps.get_model(self.model)


def _per_room() -> int:
    return settings.DEMO_KEEP_PER_ROOM


#: ``keep`` of None: the table is only capped. The rooms, memberships and ratings grow with
#: the guests and sessions, which have budgets of their own (demo/guests.py, demo/sessions.py).
#: A "-pk" after a time breaks a tie only by chance where the pk is a random UUID: two rows saved
#: within one tick of the clock (15.6 ms on Windows before Python 3.13, a microsecond on Linux) keep
#: in no particular order. The examples' models are not the demo's to change for a sequence column
KEPT = (
    Kept("chat.Message", "room", -1, ("-created_at", "-pk")),
    Kept("chat_lv2.Message", "room", -1, ("-created_at", "-pk")),
    Kept("chat_lv3.Message", "room", -1, ("-created_at", "-pk")),
    Kept("chat_lv4.Message", "room", -1, ("-created_at", "-pk")),
    Kept("notifications.Notification", "user", 50, ("-created_at", "-pk")),
    Kept("quiz.Submission", "quiz", -1, ("-completed_at", "-pk")),
    Kept("todo.Item", None, 100, ("-timestamp", "-pk")),
    Kept("chat.Room"),
    Kept("chat_lv2.Room"),
    Kept("chat_lv3.Room"),
    Kept("chat_lv4.Room"),
    Kept("chat_lv2.Membership"),
    Kept("chat_lv3.Membership"),
    Kept("chat_lv4.Membership"),
    Kept("rating.Rating"),
)


def _keep(kept: Kept) -> int | None:
    # -1: as many as DEMO_KEEP_PER_ROOM says, read when it is used (a test overrides it)
    return _per_room() if kept.keep == -1 else kept.keep


def _by_model() -> dict[type[models.Model], Kept]:
    return {kept.model_class: kept for kept in KEPT}


def refuse_past_the_cap(sender, instance: models.Model, **kwargs) -> None:
    if not instance._state.adding:
        return
    if sender._default_manager.count() >= settings.DEMO_ROWS_MAX:
        log.warning("%s holds DEMO_ROWS_MAX rows; refusing a new one", sender._meta.label)
        raise StorageFull(sender._meta.label)


def trim(kept: Kept, group_value=None) -> int:
    """Delete the rows of ``group_value``'s group (of the whole table without a group) past the newest ``keep``."""
    keep = _keep(kept)
    if keep is None:
        return 0
    rows = kept.model_class._default_manager.all()
    if kept.group is not None:
        rows = rows.filter(**{kept.group: group_value})
    stale = list(rows.order_by(*kept.newest).values_list("pk", flat=True)[keep:])
    if not stale:
        return 0
    deleted, _ = kept.model_class._default_manager.filter(pk__in=stale).delete()
    return deleted


def trim_after_save(sender, instance: models.Model, created: bool, **kwargs) -> None:
    if not created:
        return
    kept = _by_model().get(sender)
    if kept is not None and kept.keep is not None:
        trim(kept, getattr(instance, f"{kept.group}_id") if kept.group else None)


def trim_all() -> int:
    """Trim every group of every table in ``KEPT``; how many rows went."""
    deleted = 0
    for kept in KEPT:
        keep = _keep(kept)
        if keep is None:
            continue
        if kept.group is None:
            deleted += trim(kept)
            continue
        crowded = (
            kept.model_class._default_manager.values(kept.group)
            .annotate(rows=models.Count("pk"))
            .filter(rows__gt=keep)
            .values_list(kept.group, flat=True)
        )
        for group_value in list(crowded):
            deleted += trim(kept, group_value)
    return deleted


def connect() -> None:
    for kept in KEPT:
        model = kept.model_class
        pre_save.connect(refuse_past_the_cap, sender=model, dispatch_uid=f"demo.retention.cap.{kept.model}")
        if kept.keep is not None:
            post_save.connect(trim_after_save, sender=model, dispatch_uid=f"demo.retention.trim.{kept.model}")


def disconnect() -> None:
    for kept in KEPT:
        model = kept.model_class
        pre_save.disconnect(sender=model, dispatch_uid=f"demo.retention.cap.{kept.model}")
        post_save.disconnect(sender=model, dispatch_uid=f"demo.retention.trim.{kept.model}")
