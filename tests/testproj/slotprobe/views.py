from django.shortcuts import render


def index(request):
    # ?other=1: another page with the frame's class and id, no host and no fill
    # ?otherhost=1: another page whose host draws the frame's class and id with no fill
    if request.GET.get("otherhost"):
        return render(request, "slotprobe/otherhost.html")
    return render(request, "slotprobe/other.html" if request.GET.get("other") else "slotprobe/page.html")
