"""Settings for the benchmarks: the test project plus the bench app.

BENCH_LAYER picks the channel layer: memory (default), nats or redis.
"""

import importlib.util
import os

from testproj.settings import *  # noqa: F401,F403
from testproj.settings import DATABASES, INSTALLED_APPS, WIREVIEW

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, ".data")
os.makedirs(_DATA, exist_ok=True)

INSTALLED_APPS = list(INSTALLED_APPS) + ["bench.benchapp"]
if importlib.util.find_spec("daphne") is None:
    # The daphne app only backs runserver. Windows ARM64 cannot install daphne at all
    # (cryptography ships no win_arm64 wheel), and the benchmark runs uvicorn there.
    INSTALLED_APPS.remove("daphne")
_LAYER = os.environ.get("BENCH_LAYER", "memory")
if _LAYER == "nats":
    # channels-nats: one NATS server links several server processes (pip install -e ../channels-nats)
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_nats.NatsChannelLayer",
            "CONFIG": {"servers": [os.environ.get("NATS_URL", "nats://127.0.0.1:4222")]},
        }
    }
elif _LAYER == "redis":
    # channels_redis, the reference cross-process layer, for a like-for-like comparison with NATS
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")]},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
WIREVIEW = {**WIREVIEW, "AUTO_GENERATE_STUBS": False}
DATABASES["default"]["NAME"] = os.path.join(_DATA, "bench.sqlite3")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"level": "WARNING"},
    "loggers": {"wireview": {"level": "WARNING"}, "django": {"level": "WARNING"}},
}
