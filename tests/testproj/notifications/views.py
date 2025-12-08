from django.shortcuts import render


def index(request):
    """Notifications demo page."""
    return render(request, "notifications/index.html")
