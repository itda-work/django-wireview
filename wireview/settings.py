from django.conf import settings

from .schemas import AutoBroadcast

DEBUG = settings.DEBUG
DEFAULT = {
    "TRANSPILER_CACHE_SIZE": 1024,
    "USE_HTML_DIFF": True,
    "USE_HMIN": False,
    "BOOST_PAGES": False,
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
    "SYNC_TRANSITION_WARNING_THRESHOLD": 2,
    "SYNC_TRANSITION_ERROR_THRESHOLD": 3,
    # Signed component state (data-state)
    "STATE_MAX_AGE": 14 * 24 * 3600,  # 14 days, like Phoenix LiveView's session default
    "STATE_REFRESH_AFTER": None,  # None = STATE_MAX_AGE // 2. Must stay below STATE_MAX_AGE
    "STATE_ACCEPT_LEGACY": False,  # Accept pre-v1 (unbound) state formats during a rollout
    # Type stub generation
    "AUTO_GENERATE_STUBS": True,  # Auto-generate .pyi stubs in DEBUG mode
    # Telemetry signals (wireview.telemetry)
    "TELEMETRY": False,
    # Load each app's static/<app_label>/hooks/*.js from {% wireview_header %}
    "COLLECT_HOOKS": True,
}

WIREVIEW = DEFAULT | getattr(settings, "WIREVIEW", {})
LOGIN_URL = settings.LOGIN_URL

TRANSPILER_CACHE_SIZE: int = WIREVIEW["TRANSPILER_CACHE_SIZE"]
USE_HTML_DIFF: bool = WIREVIEW["USE_HTML_DIFF"]
USE_HMIN: bool = WIREVIEW["USE_HMIN"]
BOOST_PAGES: bool = WIREVIEW["BOOST_PAGES"]
AUTO_BROADCAST: AutoBroadcast = WIREVIEW["AUTO_BROADCAST"]

# Signing key material. Reached through wireview.core.signing rather than directly,
# so that every signing site shares one policy and a rotation needs no restart.
SIGNING_KEY: str | None = WIREVIEW["SIGNING_KEY"]
SIGNING_KEY_FALLBACKS: list[str] | None = WIREVIEW["SIGNING_KEY_FALLBACKS"]

# Upload settings
UPLOAD_TEMP_DIR: str | None = WIREVIEW["UPLOAD_TEMP_DIR"]
UPLOAD_MAX_FILE_SIZE: int = WIREVIEW["UPLOAD_MAX_FILE_SIZE"]
UPLOAD_CHUNK_SIZE: int = WIREVIEW["UPLOAD_CHUNK_SIZE"]
UPLOAD_TOKEN_MAX_AGE: int = WIREVIEW["UPLOAD_TOKEN_MAX_AGE"]

# Debug settings for async/sync transition tracking
DEBUG_SYNC_TRANSITIONS: bool = WIREVIEW["DEBUG_SYNC_TRANSITIONS"]
SYNC_TRANSITION_WARNING_THRESHOLD: int = WIREVIEW["SYNC_TRANSITION_WARNING_THRESHOLD"]
SYNC_TRANSITION_ERROR_THRESHOLD: int = WIREVIEW["SYNC_TRANSITION_ERROR_THRESHOLD"]

# Signed component state (wireview.core.state)
STATE_MAX_AGE: int = WIREVIEW["STATE_MAX_AGE"]
# A render re-issues the token once it is this old, even with an unchanged state.
# Half the lifetime by default, so a page that renders at all keeps a valid token.
STATE_REFRESH_AFTER: int = WIREVIEW["STATE_REFRESH_AFTER"] or STATE_MAX_AGE // 2
STATE_ACCEPT_LEGACY: bool = WIREVIEW["STATE_ACCEPT_LEGACY"]

# Type stub generation
AUTO_GENERATE_STUBS: bool = WIREVIEW["AUTO_GENERATE_STUBS"]

# Telemetry signals
TELEMETRY: bool = WIREVIEW["TELEMETRY"]

# JavaScript hook files an app ships (wireview.features.hooks). Turn it off in a
# project that puts the same files through its own bundler.
COLLECT_HOOKS: bool = WIREVIEW["COLLECT_HOOKS"]
