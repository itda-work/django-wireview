"""wireview's settings: ``settings.WIREVIEW`` on top of ``DEFAULT``.

Read when used, not when this module is imported (#100): ``settings.STATE_MAX_AGE``
looks the value up, so ``override_settings(WIREVIEW={...})`` reaches every read
site. The merged dict is cached against the ``settings.WIREVIEW`` object it came
from, so replacing that object (as ``override_settings`` does) is seen at once and
a running project pays an identity check.

A few settings only mean something at startup, and changing them later does
nothing: ``AUTO_BROADCAST`` decides which model signals get receivers,
``DEBUG_SYNC_TRANSITIONS`` and its thresholds install the detector,
``AUTO_GENERATE_STUBS`` runs when the app is ready, and ``TELEMETRY`` is the
initial state that ``wireview.telemetry.enable()`` changes at runtime.

Assigning a setting on this module (``monkeypatch.setattr``) would pin a value
that later reads, and later overrides, could not get past -- a monkeypatch's undo
pins the old one. The module refuses it; use ``override_settings``.
"""

from __future__ import annotations

import os
import sys
import types
import typing as t

from django.conf import settings as django_settings

from .schemas import AutoBroadcast

DEFAULT: dict[str, t.Any] = {
    # Links and forms navigate without a full page load (static/wireview/wireview-boost.js)
    "BOOST_PAGES": False,
    # Refuse a socket whose Origin is not in ALLOWED_HOSTS (wireview.core.origin, #96)
    "CHECK_ORIGIN": True,
    "AUTO_BROADCAST": AutoBroadcast(),
    # Signing (wireview.core.signing). None = Django's SECRET_KEY / SECRET_KEY_FALLBACKS
    "SIGNING_KEY": None,
    "SIGNING_KEY_FALLBACKS": None,
    # Upload settings
    "UPLOAD_TEMP_DIR": None,  # Where chunked uploads land. None = system temp dir; created if missing
    "UPLOAD_MAX_FILE_SIZE": 10 * 1024 * 1024,  # 10MB default
    "UPLOAD_CHUNK_SIZE": 64 * 1024,  # 64KB default
    "UPLOAD_TOKEN_MAX_AGE": 3600,  # 1 hour. Also how long an abandoned chunk file survives a sweep
    # Debug settings for async/sync transition tracking
    "DEBUG_SYNC_TRANSITIONS": False,
    "DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD": 2,
    "DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD": 3,
    # Signed component state (data-state)
    "STATE_MAX_AGE": 14 * 24 * 3600,  # 14 days, like Phoenix LiveView's session default
    "STATE_REFRESH_AFTER": None,  # None = STATE_MAX_AGE // 2. Must stay below STATE_MAX_AGE
    # Type stub generation
    "AUTO_GENERATE_STUBS": True,  # Auto-generate .pyi stubs in DEBUG mode
    # Telemetry signals (wireview.telemetry)
    "TELEMETRY": False,
    # Check the promise of Meta.shared_render: a render that reads the viewer raises,
    # and a render taken from another connection is rendered again and compared.
    # None = DEBUG (and always under wireview.testing)
    "VERIFY_SHARED_RENDER": None,
    # A template the dev server's autoreloader saw change makes every open page of this
    # process join its components again (wireview.core.template_reload, #180).
    # None = DEBUG
    "REJOIN_ON_TEMPLATE_CHANGE": None,
    # Attribute each statement a render, handler or task runs to its template line or
    # property, and log it to wireview.queries (wireview.debug.render_queries, #182).
    # None = DEBUG
    "DEBUG_RENDER_QUERIES": None,
    # Where those statements are written, one JSON line per render, for the editor
    # (wireview.debug.render_queries_file, #188). Only while DEBUG_RENDER_QUERIES and
    # DEBUG are on. None = BASE_DIR/.wireview/render-queries, a path, or False (off)
    "DEBUG_RENDER_QUERIES_DIR": None,
    # Load each app's static/<app_label>/hooks/*.js from {% wireview_header %}
    "COLLECT_HOOKS": True,
    # Client reconnect backoff, in milliseconds (static/wireview/reconnect.mjs, #124).
    # The first retry waits MIN_DELAY plus up to JITTER (drawn once per page), each
    # later one GROW_FACTOR times longer, never more than MAX_DELAY.
    "RECONNECT_MIN_DELAY_MS": 1000,
    "RECONNECT_JITTER_MS": 4000,
    "RECONNECT_MAX_DELAY_MS": 10000,
    "RECONNECT_GROW_FACTOR": 1.3,
}

#: Keys that existed and are gone, and what to do instead. ``wireview.W014``
#: names them, so an upgrade does not leave a setting that silently does nothing.
REMOVED: dict[str, str] = {
    "TRANSPILER_CACHE_SIZE": "Removed in #119: it sized the cache of the inline-script transpiler #90 retired.",
    "SYNC_TRANSITION_WARNING_THRESHOLD": "Renamed in #119: DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD.",
    "SYNC_TRANSITION_ERROR_THRESHOLD": "Renamed in #119: DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD.",
    "STATE_ACCEPT_LEGACY": "Removed in #99: a page with a pre-v2 state reloads instead.",
    "USE_HTML_DIFF": "Removed in #99: diffs are always on.",
    "USE_HMIN": (
        "Removed in #100: django-hmin stripped the diff markers, so every change sent the "
        "component's whole HTML. Compress the socket instead (permessage-deflate)."
    ),
}

#: The ``settings.WIREVIEW`` object the cache was built from, and the result.
_cache: tuple[object, dict[str, t.Any]] | None = None
_MISSING = object()


def _wireview() -> dict[str, t.Any]:
    """``DEFAULT`` with ``settings.WIREVIEW`` on top, rebuilt when that object changes.

    Keyed by identity rather than by a signal, so any way of replacing the
    setting -- ``override_settings``, pytest-django's ``settings``, a monkeypatch
    of ``django.conf.settings`` -- is seen by the next read.
    """
    global _cache
    source = getattr(django_settings, "WIREVIEW", _MISSING)
    cache = _cache
    if cache is None or cache[0] is not source:
        configured: dict[str, t.Any] = {} if source is _MISSING else t.cast(dict, source)
        cache = _cache = (source, DEFAULT | configured)
    return cache[1]


def __getattr__(name: str) -> t.Any:
    if name == "WIREVIEW":
        return _wireview()
    if name in ("DEBUG", "LOGIN_URL"):
        return getattr(django_settings, name)
    if name == "STATE_REFRESH_AFTER":
        # A render re-issues the token once it is this old, even with an unchanged
        # state. Half the lifetime by default, so a page that renders keeps a valid one.
        values = _wireview()
        return values["STATE_REFRESH_AFTER"] or values["STATE_MAX_AGE"] // 2
    if name in DEFAULT:
        return _wireview()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _SettingsModule(types.ModuleType):
    """Refuses an assignment that would pin a setting (see the module docstring)."""

    def __setattr__(self, name: str, value: t.Any) -> None:
        if name.isupper() and name not in ("DEFAULT", "REMOVED"):
            raise AttributeError(
                f"wireview.settings.{name} is read from settings.WIREVIEW when used; change it with "
                f"override_settings(WIREVIEW={{...}}) instead of assigning it here (#100)."
            )
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _SettingsModule


if t.TYPE_CHECKING:
    DEBUG: bool
    LOGIN_URL: str
    WIREVIEW: dict[str, t.Any]
    BOOST_PAGES: bool
    CHECK_ORIGIN: bool
    AUTO_BROADCAST: AutoBroadcast
    SIGNING_KEY: str | None
    SIGNING_KEY_FALLBACKS: list[str] | None
    UPLOAD_TEMP_DIR: str | None
    UPLOAD_MAX_FILE_SIZE: int
    UPLOAD_CHUNK_SIZE: int
    UPLOAD_TOKEN_MAX_AGE: int
    DEBUG_SYNC_TRANSITIONS: bool
    DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD: int
    DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD: int
    STATE_MAX_AGE: int
    STATE_REFRESH_AFTER: int
    AUTO_GENERATE_STUBS: bool
    TELEMETRY: bool
    REJOIN_ON_TEMPLATE_CHANGE: bool | None
    DEBUG_RENDER_QUERIES: bool | None
    DEBUG_RENDER_QUERIES_DIR: str | os.PathLike[str] | bool | None
    COLLECT_HOOKS: bool
    RECONNECT_MIN_DELAY_MS: int
    RECONNECT_JITTER_MS: int
    RECONNECT_MAX_DELAY_MS: int
    RECONNECT_GROW_FACTOR: float
