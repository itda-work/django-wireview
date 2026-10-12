"""The demo's housekeeping, as one long-running process (the janitor service in compose.yaml).

- every PULSE seconds: the dashboard's pulse (demo/seed.py)
- every SWEEP seconds: delete guests older than DEMO_GUEST_MAX_AGE_HOURS and expired sessions
- once a day at DEMO_RESET_AT: the reset (demo_reset)

A loop and not cron: the image has no cron, and the loop is the whole schedule in one file.
"""

import datetime as dt
import logging
import signal
import threading

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone

from demo.guests import PREFIX
from demo.seed import pulse, reset

log = logging.getLogger("demo.janitor")

PULSE = 20
SWEEP = 600


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


class Command(BaseCommand):
    help = "Run the demo's housekeeping until stopped: the dashboard's pulse, guest expiry, the daily reset."

    def handle(self, *args, **options):
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        now = timezone.localtime()
        reset_at = next_reset(now, settings.DEMO_RESET_AT)
        swept_at = now
        log.info("janitor started; next reset at %s", reset_at.isoformat())
        while not stop.wait(PULSE):
            now = timezone.localtime()
            try:
                if now >= reset_at:
                    reset()
                    log.info("reset the demo to its seed data")
                    reset_at = next_reset(now, settings.DEMO_RESET_AT)
                if (now - swept_at).total_seconds() >= SWEEP:
                    sweep()
                    swept_at = now
                pulse()
            except Exception:
                # A locked database or a broker hiccup: the next round tries again
                log.exception("janitor round failed")
        log.info("janitor stopped")
