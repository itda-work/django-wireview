from django.shortcuts import render


def index(request):
    return render(request, "reloadprobe/page.html")


def away(request):
    """Another page with a counter of its own, a boosted link away from the first."""
    return render(request, "reloadprobe/away.html")
