"""Views for LiveComponent test app."""

from django.shortcuts import render


def dashboard(request):
    """Render the dashboard page."""
    return render(request, "livecomp/page.html")
