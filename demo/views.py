import asyncio
import uuid

from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from .catalog import EXAMPLES

ROUND_TRIP_TIMEOUT = 2  # seconds


def index(request: HttpRequest) -> HttpResponse:
    """The list of examples."""
    return render(
        request,
        "demo/index.html",
        {"examples": EXAMPLES, "reset_at": f"{settings.DEMO_RESET_AT} ({settings.TIME_ZONE})"},
    )


def slots(request: HttpRequest) -> HttpResponse:
    """The slots example has components and no page of its own; this is one that uses all four."""
    team = [
        {"name": "Ada", "role": "Engineer"},
        {"name": "Grace", "role": "Admiral"},
        {"name": "Linus", "role": "Kernel"},
    ]
    return render(request, "demo/slots.html", {"team": team})


def health(request: HttpRequest) -> JsonResponse:
    """Liveness: the process answers."""
    return JsonResponse({"status": "ok"})


@transaction.non_atomic_requests
async def ready(request: HttpRequest) -> JsonResponse:
    """Readiness: the channel layer carries a message (docs/DEPLOYMENT.md, "Readiness")."""
    layer = get_channel_layer()
    if layer is None:
        return JsonResponse({"status": "unready", "reason": "no channel layer"}, status=503)
    group = f"readiness.{uuid.uuid4().hex}"
    try:
        async with asyncio.timeout(ROUND_TRIP_TIMEOUT):
            channel = await layer.new_channel()
            await layer.group_add(group, channel)
            try:
                await layer.group_send(group, {"type": "readiness.ping"})
                await layer.receive(channel)
            finally:
                await layer.group_discard(group, channel)
    except Exception as error:
        return JsonResponse({"status": "unready", "reason": repr(error)}, status=503)
    return JsonResponse({"status": "ready"})
