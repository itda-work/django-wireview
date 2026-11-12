from django.shortcuts import render


def index(request):
    return render(request, "domprobe/page.html")


def streams(request):
    return render(request, "domprobe/streams_page.html")
