from django.shortcuts import render


def index(request):
    # ?other=1: another page with the frame's class and id, no host and no fill
    return render(request, "slotprobe/other.html" if request.GET.get("other") else "slotprobe/page.html")
