"""App configuration for livecomp test app."""

from django.apps import AppConfig


class LivecompConfig(AppConfig):
    """Configuration for LiveComponent test app."""

    name = "examples.livecomp"
    verbose_name = "LiveComponent Test App"

    def ready(self):
        """Import components when app is ready."""
        from . import components  # noqa: F401
