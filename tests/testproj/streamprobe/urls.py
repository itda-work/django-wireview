from django.shortcuts import render
from django.urls import path

app_name = "streamprobe"


def index(request):
    """``?size=`` rows per page and ``?delay=`` seconds before the first (#112)."""
    context = {"size": int(request.GET.get("size", 15)), "delay": float(request.GET.get("delay", 0))}
    return render(request, "streamprobe/page.html", context)


urlpatterns = [path("", index, name="index")]
