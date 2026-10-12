from django.core.management.base import BaseCommand

from demo.management.commands.demo_janitor import reset_and_compact


class Command(BaseCommand):
    help = "Empty the examples' tables, delete guests and sessions, write the seed data and compact the database."

    def handle(self, *args, **options):
        reset_and_compact()
        self.stdout.write("The demo is back to its seed data.")
