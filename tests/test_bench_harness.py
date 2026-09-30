"""The benchmark has to be able to run, and nothing else checks that it can.

CI does not run ``make bench``, so the harness can rot in silence. It did: 0.3.0 made a
project that declares a live_session refuse legacy tokens whatever STATE_ACCEPT_LEGACY
says, the bench signed its join state the legacy way, and testproj -- the project the
bench runs on -- declares one. Every join came back as ``reload`` and the WebSocket half
of the benchmark died at connection zero.
"""

import pytest
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview.consumer import WireviewConsumer

# Through the consumer: channels closes stale DB connections on every message
# (channels.db), which needs the database even when the test never queries it.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]


async def test_the_bench_joins_with_a_state_a_project_with_boundaries_accepts():
    import testproj.livesession.live_sessions  # noqa: F401 -- the condition this guards

    from bench.ws import join_state
    from wireview.core.live_session import all_live_sessions

    assert all_live_sessions(), "testproj declares live_sessions; without one this test proves nothing"

    with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "bench.benchapp"]):
        communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), "/__wireview__")
        communicator.scope["user"] = AnonymousUser()
        connected, _ = await communicator.connect()
        assert connected
        try:
            await communicator.send_json_to(
                {"command": "join", "payload": {"name": "BenchList", "state": join_state(0, 5), "children": {}}}
            )
            first = await communicator.receive_json_from(timeout=5)
        finally:
            await communicator.disconnect()

    assert first["command"] == "render", first


async def test_the_in_process_bench_runs_every_scenario():
    # The bench reaches into internals (WireviewMeta._collect_context, the consumer's
    # render path) and neither pyright nor the rest of the suite reads bench/. #145
    # removed _get_context_async after looking for callers everywhere but here, and
    # `make bench` died on it. Small sizes: this asserts that it runs, not what it measures.
    from bench import payload

    with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "bench.benchapp"]):
        results = await payload.run(items=3, iterations=1, long_items=4, long_iterations=1, copies=1)

    assert results["timing"]["list.template_render_ms"] >= 0
    assert results["payload_bytes"]["list.first_render"] > 0
