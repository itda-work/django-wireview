from django.shortcuts import render


def index(request):
    return render(request, "islandprobe/page.html", {"board": True, "late": "late" in request.GET})


def again(request):
    """The board again, under the same id: a boosted move here keeps its element and its island."""
    return render(request, "islandprobe/page.html", {"board": True, "late": False})


def holder(request, which):
    return render(request, "islandprobe/holders.html", {"holder": f"holder-{which}"})


def sides(request, which):
    return render(request, "islandprobe/sides.html", {"a_holds": which == "a", "b_holds": which == "b"})


def elsewhere(request):
    return render(request, "islandprobe/page.html", {"board": False, "late": False})
