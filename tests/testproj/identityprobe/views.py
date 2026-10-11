"""Pages for the socket identity E2E suite (#209).

Two pages outside every boundary and two inside ``ip-inside``, each with an
``IdentityCard``, and the views another tab uses to change who is logged in:
a login, a logout, and a CSRF rotation that leaves the login alone. ``stale/``
is a page whose components are always for someone else.
"""

from django.contrib.auth import get_user_model, login, logout
from django.http import HttpResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import render
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from wireview.core.live_session import AUTH_GENERATION_KEY, AUTH_USER_ID_KEY

from .live_sessions import inside

#: What the CSRF-checked view took, in order: (text, username)
POSTED: list[tuple[str, str]] = []


def page_a(request):
    return render(request, "identityprobe/page.html", {"name": "a"})


def page_b(request):
    return render(request, "identityprobe/page.html", {"name": "b"})


@inside.view
def inside_a(request):
    return render(request, "identityprobe/page.html", {"name": "inside-a"})


@inside.view
def inside_b(request):
    return render(request, "identityprobe/page.html", {"name": "inside-b"})


def stale(request):
    """A page always rendered for someone the browser's cookies do not name.

    What a page looks like when the cookies never reach the socket (a cookie
    path that does not cover it): neither a new socket nor a reload makes the
    page and the connection agree. The page's own ``user`` is read first, from
    the real session; only the components' states are signed for the other login.
    """
    request.user.get_username()
    real = request.session
    request.session = {AUTH_USER_ID_KEY: "-1", AUTH_GENERATION_KEY: "elsewhere"}
    try:
        return render(request, "identityprobe/page.html", {"name": "stale"})
    finally:
        request.session = real


def sign_in(request, name: str):
    """Log in as ``name``: Django cycles the session and rotates the CSRF secret."""
    user, _ = get_user_model().objects.get_or_create(username=name)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponse(format_html('<p data-testid="done">signed in {}</p>', name))


def sign_out(request):
    logout(request)
    return HttpResponse('<p data-testid="done">signed out</p>')


def rotate(request):
    """A new CSRF secret for the same login, as ``rotate_token`` gives one."""
    rotate_token(request)
    return HttpResponse('<p data-testid="done">rotated</p>')


@require_POST
def post(request):
    """Checked by CsrfViewMiddleware like any view: a missing or wrong token never gets here."""
    text = request.POST.get("text", "")
    POSTED.append((text, request.user.get_username()))
    return HttpResponse(format_html('<p data-testid="posted">posted {}</p>', text))
