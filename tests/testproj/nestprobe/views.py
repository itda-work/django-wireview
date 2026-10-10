from django.shortcuts import render


def index(request):
    # ?hidden=1: the page starts with the nest hiding the leaf
    # ?anon=1: the root also draws a component without an id (#204)
    # ?ignore=1: the root draws one without an id under wire-update="ignore" once its note is set (#204)
    return render(
        request,
        "nestprobe/page.html",
        {
            "shown": not request.GET.get("hidden"),
            "anon": bool(request.GET.get("anon")),
            "ignore": bool(request.GET.get("ignore")),
        },
    )
