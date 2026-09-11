from django.apps import AppConfig


class HooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "examples.hooks"

    def ready(self):
        from . import live  # noqa: F401
