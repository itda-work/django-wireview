from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class WireviewConfig(AppConfig):
    name = "wireview"
    verbose_name = "Django Wireview"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out

        from . import auto_broadcast  # noqa
        from .checks import register_checks
        from .core.live_session import _on_user_logged_out

        # ``live`` holds components, ``live_sessions`` the page boundaries they are
        # mounted inside. Both have to be imported before the checks run, and the
        # boundaries before the first request: a name that is not in the registry
        # reads as "unknown live_session" and turns a good page into a reload.
        autodiscover_modules("live_sessions")
        autodiscover_modules("live")

        # A logout retires the connections it authenticated (#58, AC6). Registered
        # unconditionally: a project with no live_session has no subscribers, so the
        # publish reaches nobody and costs one no-op on the broker.
        user_logged_out.connect(_on_user_logged_out, dispatch_uid="wireview.live_session.logout")

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
