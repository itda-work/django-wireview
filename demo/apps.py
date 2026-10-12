from importlib import import_module

from django.apps import AppConfig


class DemoConfig(AppConfig):
    name = "demo"
    verbose_name = "wireview demo"

    def ready(self) -> None:
        from .guests import BUTTON, EXAMPLES

        # The examples' sign-in pages list their fixed cast from DEMO_USERS, read on every
        # request. On the public site the cast is one "guest" button, which demo/urls.py
        # answers with a guest account of the visitor's own (demo/guests.py).
        for example in EXAMPLES:
            import_module(f"examples.{example}.views").DEMO_USERS = BUTTON

        # What visitors write is kept within a budget: the newest rows of each room, and no
        # table past DEMO_ROWS_MAX (demo/retention.py)
        from . import retention

        retention.connect()
