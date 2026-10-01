from django.shortcuts import render
from django.urls import path

app_name = "streamprobe"


def index(request):
    """``?size=`` rows per page and ``?delay=`` seconds before the first (#112).
    ``?away=1`` is the page without the probe, to leave it and come back (#146).
    ``?pair=1`` adds a second probe below, and ``?nest=1`` a LiveComponent ahead of
    the probe's ticks list: both name a stream ``ticks`` too. ``?seeded=1`` renders
    the SeedChild from the start, so it is on the page when the probe joins."""
    context = {
        "size": int(request.GET.get("size", 15)),
        "delay": float(request.GET.get("delay", 0)),
        "away": bool(request.GET.get("away")),
        "pair": bool(request.GET.get("pair")),
        "nest": bool(request.GET.get("nest")),
        "seeded": bool(request.GET.get("seeded")),
    }
    return render(request, "streamprobe/page.html", context)


urlpatterns = [path("", index, name="index")]
