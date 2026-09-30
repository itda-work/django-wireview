"""testproj on the Redis channel layer (channels_redis).

One of the two layers CI's E2E runs on, next to nats (#130).

    uv run pytest --ds=testproj.settings_redis -m e2e
"""

import os

os.environ["WIREVIEW_TEST_LAYER"] = "redis"

from .settings import *  # noqa: E402,F401,F403
