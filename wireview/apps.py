from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class WireviewConfig(AppConfig):
    name = "wireview"
    verbose_name = "Django Wireview"

    def ready(self):
        from . import auto_broadcast  # noqa
        from .checks import register_checks

        autodiscover_modules("live")

        # Components must be imported before the checks run
        register_checks()

        # Auto-generate type stubs in DEBUG mode
        self._auto_generate_stubs()

    def _auto_generate_stubs(self) -> None:
        """Generate type stubs for components if enabled in DEBUG mode."""
        from . import settings

        if not settings.DEBUG:
            return

        if not settings.AUTO_GENERATE_STUBS:
            return

        try:
            from .management.commands.wireview_stubs import generate_all_stubs

            generate_all_stubs(quiet=True)
        except Exception:
            # Silently ignore errors during stub generation
            # This should not break the server startup
            pass
