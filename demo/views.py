import asyncio
import logging
import uuid

from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from .catalog import EXAMPLES

log = logging.getLogger("demo.ready")

ROUND_TRIP_TIMEOUT = 2  # seconds


def index(request: HttpRequest) -> HttpResponse:
    """The list of examples."""
    return render(
        request,
        "demo/index.html",
        {"examples": EXAMPLES, "reset_at": f"{settings.DEMO_RESET_AT} ({settings.TIME_ZONE})"},
    )


def health(request: HttpRequest) -> JsonResponse:
    """Liveness: the process answers."""
    return JsonResponse({"status": "ok"})


@transaction.non_atomic_requests
async def ready(request: HttpRequest) -> JsonResponse:
    """Readiness: the channel layer carries a message, and the rate-limit counters answer
    (docs/DEPLOYMENT.md, "Readiness").

    The path is public, so the answer says only ready or not; why goes to the log.
    """
    try:
        async with asyncio.timeout(ROUND_TRIP_TIMEOUT):
            await _layer_round_trip()
            await cache.aset("ready", 1, 10)
    except Exception:
        log.exception("not ready")
        return JsonResponse({"status": "unready"}, status=503)
    return JsonResponse({"status": "ready"})


async def _layer_round_trip() -> None:
    layer = get_channel_layer()
    if layer is None:
        raise RuntimeError("no channel layer")
    group = f"readiness.{uuid.uuid4().hex}"
    channel = await layer.new_channel()
    await layer.group_add(group, channel)
    try:
        await layer.group_send(group, {"type": "readiness.ping"})
        await layer.receive(channel)
    finally:
        await layer.group_discard(group, channel)
