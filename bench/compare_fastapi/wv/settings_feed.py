"""The comparison's settings with the feed app, on the channel layer ``BENCH_LAYER`` names (#178).

``memory`` (default), ``redis`` (``REDIS_URL``) or ``nats`` (``NATS_URL``). The board's
measurement keeps settings.py as it is: its lines are counted (loc.py).

The two brokered layers take docs/DEPLOYMENT.md's ``capacity`` of 1,500. channels_redis keeps
every process-local channel of a process in one Redis key, and at its default of 100 a
thousand joins at once drop their own session mail (their ``joined``).
"""

import os

from bench.compare_fastapi.wv.settings import *  # noqa: F403
from bench.compare_fastapi.wv.settings import INSTALLED_APPS

INSTALLED_APPS = [*INSTALLED_APPS, "bench.compare_fastapi.wv.feed"]
ROOT_URLCONF = "bench.compare_fastapi.wv.urls_feed"

_LAYER = os.environ.get("BENCH_LAYER", "memory")
if _LAYER == "redis":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [os.environ["REDIS_URL"]], "capacity": 1500},
        }
    }
elif _LAYER == "nats":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_nats.NatsChannelLayer",
            "CONFIG": {"servers": [os.environ["NATS_URL"]], "capacity": 1500},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
