from django.shortcuts import render


def index(request):
    # ?nest=1: the probe drawn in a host's pass, and in another component's slot
    return render(request, "tempprobe/nest.html" if request.GET.get("nest") else "tempprobe/page.html")
