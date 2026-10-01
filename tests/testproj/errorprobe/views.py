from django.shortcuts import render


def index(request):
    return render(request, "errorprobe/page.html")


def late(request):
    """Components and links back to this page: each visit joins them again under their ids."""
    return render(request, "errorprobe/late.html")


def slot(request):
    """Components whose joins fail, one holding the page's own LiveComponent in its slot."""
    return render(request, "errorprobe/slot.html")
