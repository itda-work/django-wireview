from django.shortcuts import redirect, render

from .live import Showing


def index(request):
    return render(request, "index.html", context={"title": "index"})


def todo(request):
    return render(
        request,
        "todo.html",
        context={
            "title": "todo",
            # An unknown value is the default filter: the address is the user's to type
            "showing": Showing.from_query(request.GET.get("showing")),
        },
    )


def redirect_to_index(request):
    return redirect("/?frombackend=1")
