"""``manage.py wireview_gohost``: run wireview sessions behind the Go proxy.

Usage:
    python manage.py wireview_gohost --socket /tmp/wireview-gohost.sock
    goproxy -listen 127.0.0.1:8100 -backend /tmp/wireview-gohost.sock
"""

from __future__ import annotations

import asyncio
import typing as t

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Host wireview WebSocket sessions for goproxy over a Unix socket"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--socket", default="/tmp/wireview-gohost.sock", help="Unix socket path")

    def handle(self, *args: t.Any, **options: t.Any) -> None:
        from channels.routing import get_default_application

        from wireview.contrib.gohost import GoHost

        host = GoHost(get_default_application(), options["socket"])
        self.stdout.write(f"wireview gohost on {options['socket']}")
        try:
            asyncio.run(host.serve())
        except KeyboardInterrupt:
            pass
