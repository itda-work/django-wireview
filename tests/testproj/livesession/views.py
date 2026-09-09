"""Pages for the live_session E2E suite.

Three pages and two boundaries: a public page outside every session, a staff
page inside ``ls-staff``, and a members page inside ``ls-members``. Navigating
between any two of them crosses a boundary, which is what the E2E suite watches
for -- a boosted move that morphs the body would keep a socket authenticated
under the page it left.
"""

from django.contrib.auth import get_user_model, login, logout
from django.http import HttpResponse
from django.shortcuts import redirect, render

from .live_sessions import members, staff


def public(request):
    return render(request, "livesession/public.html")


def public_two(request):
    """A second page outside every boundary.

    Moving between this and ``public`` stays inside "no boundary", which is what
    lets the E2E suite prove the check does not turn every boosted navigation
    into a full page load.
    """
    return render(request, "livesession/public2.html")


@staff.view
def staff_page(request):
    return render(request, "livesession/staff.html")


@members.view
def members_page(request):
    return render(request, "livesession/members.html")


def redirect_to_public(request):
    """A URL inside the boundary whose response is a page outside it.

    The client checks the *response*, not the URL it asked for, so this is what
    proves a redirect chain cannot smuggle a page across the boundary.
    """
    return redirect("/livesession/public/")


def sign_in(request):
    """Log in as the fixture user. Exists for the E2E suite, which drives a browser."""
    user, _ = get_user_model().objects.get_or_create(username="ls-e2e")
    user.is_staff = request.GET.get("staff", "1") == "1"
    user.set_unusable_password()
    user.save()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(request.GET.get("next") or "/livesession/public/")


def sign_out(request):
    logout(request)
    return HttpResponse("bye")
