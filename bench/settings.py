"""Settings for the benchmarks: the test project plus the bench app, no Redis."""

import os

from testproj.settings import *  # noqa: F401,F403
from testproj.settings import DATABASES, INSTALLED_APPS, WIREVIEW

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, ".data")
os.makedirs(_DATA, exist_ok=True)

INSTALLED_APPS = list(INSTALLED_APPS) + ["bench.benchapp"]
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
WIREVIEW = {**WIREVIEW, "AUTO_GENERATE_STUBS": False}
DATABASES["default"]["NAME"] = os.path.join(_DATA, "bench.sqlite3")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"level": "WARNING"},
    "loggers": {"wireview": {"level": "WARNING"}, "django": {"level": "WARNING"}},
}
