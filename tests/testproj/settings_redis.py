"""testproj on the Redis channel layer (channels_redis).

The fallback while channels-nats is not installable everywhere; CI uses it.

    uv run pytest --ds=testproj.settings_redis -m e2e
"""

import os

os.environ["WIREVIEW_TEST_LAYER"] = "redis"

from .settings import *  # noqa: E402,F401,F403
