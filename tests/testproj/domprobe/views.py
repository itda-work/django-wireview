from django.shortcuts import render


def index(request):
    return render(request, "domprobe/page.html")


def streams(request):
    return render(request, "domprobe/streams_page.html")


def table(request):
    return render(request, "domprobe/table_page.html")


def payload(request):
    return render(request, "domprobe/payload_page.html")
