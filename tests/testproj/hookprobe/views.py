from django.shortcuts import render


def index(request):
    return render(request, "hookprobe/page.html")


def elsewhere(request):
    return render(request, "hookprobe/elsewhere.html")
