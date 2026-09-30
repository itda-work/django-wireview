from django.shortcuts import render


def page(request, name: str):
    # c has no player: moving there is how a sticky component leaves
    return render(request, "stickyprobe/page.html", {"name": name, "with_player": name != "c"})
