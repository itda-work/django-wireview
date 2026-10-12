"""The demo's housekeeping, as one long-running process (the janitor service in compose.yaml).

- every PULSE seconds: the dashboard's pulse (demo/seed.py)
- every SWEEP seconds: delete guests older than DEMO_GUEST_MAX_AGE_HOURS and expired sessions,
  fold SQLite's WAL back into the database file, and, when the file has grown past
  DEMO_DB_MAX_MB, reset the demo early
- once a day at DEMO_RESET_AT: the reset (demo_reset)
- after every reset: VACUUM, which gives deleted rows' pages back to the disk

A loop and not cron: the image has no cron, and the loop is the whole schedule in one file.

Each round that does its work touches HEARTBEAT; compose's healthcheck calls the janitor
unhealthy when the file is older than a few rounds (``docker compose ps``). A round that fails
is logged; FAILURES_TO_ALARM failures in a row are logged as an ERROR that says the cleanup has
stopped, and again every FAILURES_TO_ALARM after that.
"""

import datetime as dt
import logging
import signal
import threading
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from demo.guests import PREFIX
from demo.seed import pulse, reset

log = logging.getLogger("demo.janitor")

PULSE = 20
SWEEP = 600
FAILURES_TO_ALARM = 15  # five minutes of rounds


def heartbeat() -> Path:
    return Path(settings.DATA_DIR) / "janitor.ok"


def next_reset(now: dt.datetime, at: str) -> dt.datetime:
    hour, minute = (int(part) for part in at.split(":"))
    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today if today > now else today + dt.timedelta(days=1)


def sweep() -> None:
    cutoff = timezone.now() - dt.timedelta(hours=settings.DEMO_GUEST_MAX_AGE_HOURS)
    guests, _ = get_user_model().objects.filter(username__startswith=PREFIX, date_joined__lt=cutoff).delete()
    sessions, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
    if guests or sessions:
        log.info("swept %d rows of old guests and %d expired sessions", guests, sessions)


def database_bytes() -> int:
    """The database file and its WAL, in bytes."""
    name = Path(settings.DATABASES["default"]["NAME"])
    return sum(path.stat().st_size for path in (name, Path(f"{name}-wal")) if path.exists())


def checkpoint() -> None:
    """Fold the WAL into the database file and empty it. The app's readers can keep a WAL growing."""
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def compact() -> None:
    """Give the pages of deleted rows back to the disk."""
    with connection.cursor() as cursor:
        cursor.execute("VACUUM")
    checkpoint()


def reset_and_compact() -> None:
    reset()
    compact()


def keep_within_budget() -> None:
    """Checkpoint; if the file is still past DEMO_DB_MAX_MB, reset the demo now rather than at DEMO_RESET_AT."""
    checkpoint()
    size = database_bytes()
    if size > settings.DEMO_DB_MAX_MB * 1024 * 1024:
        log.warning("the database is %d MB, past DEMO_DB_MAX_MB; resetting the demo early", size // 2**20)
        reset_and_compact()
        log.info("the database is %d MB after the reset", database_bytes() // 2**20)


class Command(BaseCommand):
    help = "Run the demo's housekeeping until stopped: the dashboard's pulse, guest expiry, the daily reset."

    def handle(self, *args, **options):
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        now = timezone.localtime()
        reset_at = next_reset(now, settings.DEMO_RESET_AT)
        swept_at = now - dt.timedelta(seconds=SWEEP)  # the first round sweeps
        failures = 0
        heartbeat().touch()
        log.info("janitor started; next reset at %s", reset_at.isoformat())
        while not stop.wait(PULSE):
            now = timezone.localtime()
            try:
                if now >= reset_at:
                    reset_and_compact()
                    log.info("reset the demo to its seed data")
                    reset_at = next_reset(now, settings.DEMO_RESET_AT)
                if (now - swept_at).total_seconds() >= SWEEP:
                    sweep()
                    keep_within_budget()
                    swept_at = now
                pulse()
            except Exception:
                # A locked database or a broker hiccup: the next round tries again
                failures += 1
                log.exception("janitor round failed (%d in a row)", failures)
                if failures % FAILURES_TO_ALARM == 0:
                    log.error("the janitor has failed %d rounds in a row: no cleanup is happening", failures)
                continue
            failures = 0
            heartbeat().touch()
        log.info("janitor stopped")
