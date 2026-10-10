from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .live import NOTES


def index(request):
    return render(request, "deadprobe/page.html", {"notes": list(NOTES)})


@require_POST
def add(request):
    """Where the form goes when no JavaScript intercepts it: an ordinary POST, then a redirect.

    Checked for its CSRF token as any view is: the component's ``{% csrf_token %}`` (#206).
    """
    if text := request.POST.get("text"):
        NOTES.append(text)
    return redirect("deadprobe:index")
