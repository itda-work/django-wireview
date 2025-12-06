from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class WireviewConfig(AppConfig):
    name = "wireview"
    verbose_name = "Django Wireview"

    def ready(self):
        from . import auto_broadcast  # noqa

        autodiscover_modules("live")
