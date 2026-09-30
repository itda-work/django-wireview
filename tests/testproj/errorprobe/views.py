from django.shortcuts import render


def index(request):
    return render(request, "errorprobe/page.html")


def late(request):
    """Components and links back to this page: each visit joins them again under their ids."""
    return render(request, "errorprobe/late.html")
