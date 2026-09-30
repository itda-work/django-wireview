"""wireview on the NATS channel layer (channels-nats): cross-process broadcasts reach consumers.

Needs the ``channels_nats`` package and a ``nats-server`` binary (NATS_SERVER env, PATH, or ~/go/bin).
Without either this skips locally but fails in CI: channels' floor rests on these tests (#132), and a
runner without the binary once skipped them all and passed on a floor that did not work.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
import typing as t
from pathlib import Path

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from django.test import override_settings


def _missing(reason: str) -> t.NoReturn:
    """Skip on a developer's machine; fail where CI runs (GitHub Actions sets ``CI``)."""
    if os.environ.get("CI"):
        pytest.fail(f"{reason}, and CI must run these tests", pytrace=False)
    pytest.skip(reason, allow_module_level=True)


try:
    import channels_nats
except ImportError:
    _missing("channels_nats is not installed")

from wireview import Component, abroadcast  # noqa: E402
from wireview.consumer import WireviewConsumer  # noqa: E402
from wireview.core.meta import WireviewMeta  # noqa: E402
from wireview.core.rendered import PROTOCOL_VERSION  # noqa: E402
from wireview.core.state import sign_state  # noqa: E402

# The render path crosses channels' ``database_sync_to_async``: see the note in
# tests/test_diff_stability.py for why that needs the database marker here.
pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _find_nats_server() -> str | None:
    for candidate in (
        os.environ.get("NATS_SERVER"),
        shutil.which("nats-server"),
        str(Path.home() / "go/bin/nats-server"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


@pytest.fixture(scope="module")
def nats_url():
    binary = _find_nats_server()
    if binary is None:
        _missing("nats-server binary not found")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [binary, "-a", "127.0.0.1", "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.05)
    try:
        yield f"nats://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


_template: Template | None = None


class NatsProbe(Component):
    class Meta:
        template_name = "nats_probe.html"
        subscriptions = {"nats-probe"}

    count: int = 0

    async def notification(self, channel: str, **kwargs):
        self.count = kwargs.get("count", self.count + 1)

    @classmethod
    def _get_template(cls, template_name=None):
        global _template
        if _template is None:
            _template = Template('{% load wireview %}<div {% tag_header %}><p class="count">{{ count }}</p></div>')
        return _template


async def _join(nats_url: str) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    state = sign_state(NatsProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id="probe-1", count=0))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": "NatsProbe", "state": state, "children": {}}}
    )
    first = await communicator.receive_json_from(timeout=5)
    assert first["command"] == "render"
    # The join is over once ``joined`` arrives, and the subscriptions come before
    # it: what is published from here on reaches the component (#143).
    for _ in range(5):
        if (await communicator.receive_json_from(timeout=5))["command"] == "joined":
            return communicator
    raise AssertionError("the join did not end")


async def _next_render(communicator: WebsocketCommunicator) -> dict:
    for _ in range(5):
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] == "render":
            return message["payload"]["diff"]
    raise AssertionError("no render received")


@pytest.fixture(params=["prompt", "slow"])
def subscribe_pace(request, monkeypatch):
    """A broker that registers a subscription at once, and one that takes its time (#143).

    "slow" holds group_add past the render that answers the join, as a busy
    broker or runner may: a message published then reaches no subscriber.
    """
    if request.param == "slow":
        add = channels_nats.NatsChannelLayer.group_add

        async def slow(self, group, channel):
            await asyncio.sleep(0.5)
            await add(self, group, channel)

        monkeypatch.setattr(channels_nats.NatsChannelLayer, "group_add", slow)
    return request.param


def _layer_settings(url: str) -> dict:
    return {"default": {"BACKEND": "channels_nats.NatsChannelLayer", "CONFIG": {"servers": [url]}}}


@pytest.mark.asyncio
async def test_group_send_from_another_process_rerenders_the_component(nats_url, subscribe_pace):
    with override_settings(CHANNEL_LAYERS=_layer_settings(nats_url)):
        communicator = await _join(nats_url)
        other_process = channels_nats.NatsChannelLayer(servers=nats_url)
        try:
            await other_process.group_send(
                "nats-probe", {"type": "notification", "channel": "nats-probe", "kwargs": {"count": 7}}
            )
            diff = await _next_render(communicator)
            assert "7" in json.dumps(diff)
        finally:
            await other_process.close()
            await communicator.disconnect()


@pytest.mark.asyncio
async def test_abroadcast_goes_through_the_nats_layer(nats_url, subscribe_pace):
    with override_settings(CHANNEL_LAYERS=_layer_settings(nats_url)):
        communicator = await _join(nats_url)
        try:
            await abroadcast("nats-probe", count=9)
            diff = await _next_render(communicator)
            assert "9" in json.dumps(diff)
        finally:
            await communicator.disconnect()
