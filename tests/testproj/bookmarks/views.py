"""Views for the bookmarks app.

The component template under tests/testproj/bookmarks/templates/ was written by
an agent that could read only skills/wireview/. This page exists to put that
template in a real browser.
"""

from django.shortcuts import render


def index(request):
    return render(request, "bookmarks/page.html")
