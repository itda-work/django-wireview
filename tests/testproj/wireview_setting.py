"""Change a ``WIREVIEW`` setting for one test.

wireview reads its settings when used (#100), so a test changes the Django
setting and nothing else. Assigning to ``wireview.settings`` instead would pin
the value in that module and hide every later change; tests/test_settings.py
guards against it.
"""

import typing as t


def set_wireview(monkeypatch: t.Any, **values: t.Any) -> None:
    """Set keys of ``settings.WIREVIEW`` until the test ends."""
    from django.conf import settings

    monkeypatch.setattr(settings, "WIREVIEW", {**getattr(settings, "WIREVIEW", {}), **values}, raising=False)
