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
