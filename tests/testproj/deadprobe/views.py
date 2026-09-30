from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .live import NOTES


def index(request):
    return render(request, "deadprobe/page.html", {"notes": list(NOTES)})


@csrf_exempt
@require_POST
def add(request):
    """Where the form goes when no JavaScript intercepts it: an ordinary POST, then a redirect."""
    if text := request.POST.get("text"):
        NOTES.append(text)
    return redirect("deadprobe:index")
