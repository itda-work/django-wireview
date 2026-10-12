from django.shortcuts import render

# Imported for its side effect: a component is registered when its class is defined
from . import components  # noqa: F401


def gallery(request):
    """The four layout components on one page, filled by SlotsGallery."""
    return render(request, "slots/page.html")
