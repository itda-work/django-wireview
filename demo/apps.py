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
        # The slots example is components without a page or a live module, so nothing would
        # register them; the demo's slots page draws them
        import_module("examples.slots.components")
