"""testproj settings with the NATS channel layer (channels-nats) instead of Redis.

NATS_URL=nats://127.0.0.1:4222 uv run pytest --ds=testproj.settings_nats -m e2e
"""

import os

from .settings import *  # noqa: F401,F403

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_nats.NatsChannelLayer",
        "CONFIG": {"servers": [os.environ.get("NATS_URL", "nats://127.0.0.1:4222")]},
    }
}
