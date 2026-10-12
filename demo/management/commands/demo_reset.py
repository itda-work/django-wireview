from django.core.management.base import BaseCommand

from demo.seed import reset


class Command(BaseCommand):
    help = "Empty the examples' tables, delete guest accounts and sessions, and write the demo's seed data."

    def handle(self, *args, **options):
        reset()
        self.stdout.write("The demo is back to its seed data.")
