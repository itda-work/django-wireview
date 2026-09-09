"""Collect chunk files that no upload can still finish.

The chunk endpoint is stateless (#83), so nothing on the write path knows that
the component an upload belongs to is gone: a worker that dies takes its cancel
with it, and the endpoint accepts chunks for a departed component until the
token expires. What is left behind is therefore bounded by
``UPLOAD_TOKEN_MAX_AGE``, and age is the whole rule -- nothing older can belong
to an upload that could still complete.

The write path already sweeps opportunistically, once every ten minutes per
worker. This command is for a deployment that would rather run it from cron, and
for looking at what is there:

    python manage.py wireview_upload_gc
    python manage.py wireview_upload_gc --dry-run
    python manage.py wireview_upload_gc --max-age 7200
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandParser

from wireview import settings as wireview_settings
from wireview.features import upload_store


class Command(BaseCommand):
    help = "Remove chunked upload files older than WIREVIEW['UPLOAD_TOKEN_MAX_AGE']"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--max-age",
            type=int,
            default=None,
            help="Seconds a file may go untouched (default: WIREVIEW['UPLOAD_TOKEN_MAX_AGE'])",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be removed without removing it",
        )

    def handle(self, *args, **options) -> None:
        max_age: int = options["max_age"] or wireview_settings.UPLOAD_TOKEN_MAX_AGE
        root = upload_store.store_root()

        if options["dry_run"]:
            cutoff = time.time() - max_age
            stale = [
                entry
                for connection in sorted(root.iterdir())
                if connection.is_dir()
                for entry in sorted(connection.iterdir())
                if entry.is_file() and entry.stat().st_mtime < cutoff
            ]
            for entry in stale:
                self.stdout.write(f"would remove {entry}")
            self.stdout.write(self.style.SUCCESS(f"{len(stale)} file(s) older than {max_age}s in {root}"))
            return

        removed = upload_store.sweep(max_age, root=root)
        self.stdout.write(self.style.SUCCESS(f"Removed {removed} file(s) older than {max_age}s from {root}"))
