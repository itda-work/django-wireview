from django.shortcuts import render


def index(request):
    # ?hidden=1: the page starts with the nest hiding the leaf
    return render(request, "nestprobe/page.html", {"shown": not request.GET.get("hidden")})
