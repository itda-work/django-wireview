"""The demo's housekeeping, as one long-running process (the janitor service in compose.yaml).

- every PULSE seconds: the dashboard's pulse (demo/seed.py), and a look at the limits' Redis
  (``watch_limits``): past LIMITS_ALARM of its memory, an ERROR in the log, the janitor
  unhealthy (LIMITS_ALARM_FILE) and the counters' strays cleaned up
- every SWEEP seconds: delete guests older than DEMO_GUEST_MAX_AGE_HOURS and expired sessions,
  trim what visitors wrote to its budget (demo/retention.py), fold SQLite's WAL back into the
  database file, VACUUM when deleted rows left more than FREE_BYTES_TO_COMPACT of free pages,
  and, as the last resort for the disk, reset the demo early when the file has grown past
  DEMO_DB_MAX_MB
- once a day at DEMO_RESET_AT: the reset (demo_reset)
- after every reset: VACUUM, which gives deleted rows' pages back to the disk

A loop and not cron: the image has no cron, and the loop is the whole schedule in one file.

Each round that does its work touches HEARTBEAT; compose's healthcheck calls the janitor
unhealthy when the file is older than a few rounds, or while LIMITS_ALARM_FILE is there
(``docker compose ps``). A round that fails
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
from django.core.cache import caches
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from demo import limits, retention
from demo.guests import PREFIX
from demo.seed import pulse, reset

log = logging.getLogger("demo.janitor")

PULSE = 20
SWEEP = 600
FAILURES_TO_ALARM = 15  # five minutes of rounds
#: The share of the limits' Redis memory past which the janitor raises the alarm
LIMITS_ALARM = 0.8


def heartbeat() -> Path:
    return Path(settings.DATA_DIR) / "janitor.ok"


def limits_alarm_file() -> Path:
    """There while the limits' Redis is past LIMITS_ALARM; compose's healthcheck reads it."""
    return Path(settings.DATA_DIR) / "limits.full"


def clean_limits(client) -> int:
    """Delete the counters' keys that would outlive their window; how many went.

    Every key the limits write has an expiry and every lease set lapses (demo/limits.py), so
    this finds nothing unless a key lost its expiry some way the limits do not know of. Lease
    sets drop their lapsed members here too.
    """
    cleaned = 0
    names: list = []

    def clean(batch: list) -> int:
        # A round trip per thousand keys, not per key: a full Redis holds hundreds of thousands
        with client.pipeline(transaction=False) as pipe:
            for name in batch:
                pipe.ttl(name)
                pipe.type(name)
            replies = pipe.execute()
        with client.pipeline(transaction=False) as pipe:
            for name, ttl, kind in zip(batch, replies[::2], replies[1::2], strict=True):
                if ttl == -1:
                    pipe.delete(name)
                elif kind in (b"zset", "zset"):
                    pipe.zremrangebyscore(name, "-inf", limits.wall())
            return sum(pipe.execute())

    for name in client.scan_iter(match=caches["default"].make_key("limit:*"), count=1000):
        names.append(name)
        if len(names) == 1000:
            cleaned += clean(names)
            names = []
    return cleaned + (clean(names) if names else 0)


def watch_limits() -> bool:
    """Whether the limits' Redis is past LIMITS_ALARM of its memory; logs, cleans and marks it if so."""
    client = limits._redis()
    if client is None:
        return False
    memory = client.info("memory")
    used, most = memory["used_memory"], memory.get("maxmemory") or 0
    alarm = limits_alarm_file()
    if most and used >= LIMITS_ALARM * most:
        keys = client.dbsize()
        # Said first, then cleaned: the cleaning walks every key
        alarm.write_text(f"{used} of {most} bytes, {keys} keys\n")
        log.error(
            "the limits' Redis is at %d%% of its memory (%d keys): past 100%% every visitor is refused"
            " until windows expire. Raise its maxmemory or the limits (README)",
            100 * used // most,
            keys,
        )
        cleaned = clean_limits(client)
        if cleaned:
            log.warning("cleaned %d keys of the limits that would have outlived their window", cleaned)
        return True
    alarm.unlink(missing_ok=True)
    return False


def next_reset(now: dt.datetime, at: str) -> dt.datetime:
    hour, minute = (int(part) for part in at.split(":"))
    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today if today > now else today + dt.timedelta(days=1)


def sweep() -> None:
    cutoff = timezone.now() - dt.timedelta(hours=settings.DEMO_GUEST_MAX_AGE_HOURS)
    guests, _ = get_user_model().objects.filter(username__startswith=PREFIX, date_joined__lt=cutoff).delete()
    sessions, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
    trimmed = retention.trim_all()
    if guests or sessions or trimmed:
        log.info(
            "swept %d rows of old guests, %d expired sessions and %d rows past their budget", guests, sessions, trimmed
        )


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


#: Free pages past this many bytes after a sweep: VACUUM gives them back (a trimmed flood)
FREE_BYTES_TO_COMPACT = 64 * 2**20


def free_bytes() -> int:
    """The bytes of the database file's pages no row uses: what deleted rows left."""
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA freelist_count")
        (free,) = cursor.fetchone()
        cursor.execute("PRAGMA page_size")
        (page,) = cursor.fetchone()
    return free * page


def reset_and_compact() -> None:
    reset()
    compact()


def keep_within_budget() -> None:
    """Checkpoint; if the file is still past DEMO_DB_MAX_MB, reset the demo now rather than at DEMO_RESET_AT.

    The last resort for the disk: retention keeps the tables far below it, so getting here
    means something grew that no budget covers. An ERROR, then.
    """
    checkpoint()
    if free_bytes() > FREE_BYTES_TO_COMPACT:
        before = database_bytes()
        compact()
        log.info("compacted the database from %d MB to %d MB", before // 2**20, database_bytes() // 2**20)
    size = database_bytes()
    if size > settings.DEMO_DB_MAX_MB * 1024 * 1024:
        log.error(
            "the database is %d MB, past DEMO_DB_MAX_MB, which retention should never let it reach;"
            " resetting the demo early -- every room, guest and session goes. Find what grew",
            size // 2**20,
        )
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
                watch_limits()
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
