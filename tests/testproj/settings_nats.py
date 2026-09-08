"""testproj on the NATS channel layer. Kept as an explicit entry point.

``settings`` already defaults to NATS; this module pins it so a command line that
names it cannot be overridden by the environment.

    NATS_URL=nats://127.0.0.1:4222 uv run pytest --ds=testproj.settings_nats -m e2e
"""

import os

os.environ["WIREVIEW_TEST_LAYER"] = "nats"

from .settings import *  # noqa: E402,F401,F403
