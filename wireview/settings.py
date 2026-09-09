from django.conf import settings

from .schemas import AutoBroadcast

DEBUG = settings.DEBUG
DEFAULT = {
    "TRANSPILER_CACHE_SIZE": 1024,
    "USE_HTML_DIFF": True,
    "USE_HMIN": False,
    "BOOST_PAGES": False,
    "AUTO_BROADCAST": AutoBroadcast(),
    # Upload settings
    "UPLOAD_TEMP_DIR": None,  # None = system temp dir
    "UPLOAD_MAX_FILE_SIZE": 10 * 1024 * 1024,  # 10MB default
    "UPLOAD_CHUNK_SIZE": 64 * 1024,  # 64KB default
    "UPLOAD_TOKEN_MAX_AGE": 3600,  # 1 hour
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
}

WIREVIEW = DEFAULT | getattr(settings, "WIREVIEW", {})
LOGIN_URL = settings.LOGIN_URL

TRANSPILER_CACHE_SIZE: int = WIREVIEW["TRANSPILER_CACHE_SIZE"]
USE_HTML_DIFF: bool = WIREVIEW["USE_HTML_DIFF"]
USE_HMIN: bool = WIREVIEW["USE_HMIN"]
BOOST_PAGES: bool = WIREVIEW["BOOST_PAGES"]
AUTO_BROADCAST: AutoBroadcast = WIREVIEW["AUTO_BROADCAST"]

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
