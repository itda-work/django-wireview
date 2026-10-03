from django.shortcuts import render


def page(request, name: str):
    # c has no player: moving there is how a sticky component leaves. late and
    # late-root have LateSticky instead, and late-root draws its inner as a root.
    # shelf-a and shelf-b have the sticky StickyShelf, which draws ShelfLabel.
    return render(request, "stickyprobe/page.html", {"name": name, "with_player": name in ("a", "b")})
