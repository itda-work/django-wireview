from django.shortcuts import render


def index(request):
    """Search page."""
    return render(request, "search/index.html")
