from django.shortcuts import render


def index(request):
    return render(request, "errorprobe/page.html")


def late(request):
    """One component and two links back to this page: each visit joins it again under its id."""
    return render(request, "errorprobe/late.html")
