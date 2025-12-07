from django.conf import settings

from .schemas import AutoBroadcast

DEBUG = settings.DEBUG
DEFAULT = {
    "TRANSPILER_CACHE_SIZE": 1024,
    "USE_HTML_DIFF": True,
    "USE_HMIN": False,
    "BOOST_PAGES": False,
    "AUTO_BROADCAST": AutoBroadcast(),
}

WIREVIEW = DEFAULT | getattr(settings, "WIREVIEW", {})
LOGIN_URL = settings.LOGIN_URL

TRANSPILER_CACHE_SIZE: int = WIREVIEW["TRANSPILER_CACHE_SIZE"]
USE_HTML_DIFF: bool = WIREVIEW["USE_HTML_DIFF"]
USE_HMIN: bool = WIREVIEW["USE_HMIN"]
BOOST_PAGES: bool = WIREVIEW["BOOST_PAGES"]
AUTO_BROADCAST: AutoBroadcast = WIREVIEW["AUTO_BROADCAST"]
