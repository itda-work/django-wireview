from django.shortcuts import render


def index(request):
    """``?failing=1`` adds a target whose join fails."""
    return render(request, "broadcastprobe/page.html", {"failing": request.GET.get("failing") == "1"})
