from django.contrib.auth import get_user_model, login, logout
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

#: The people the demo signs in as. Open two windows as two of them to see a
#: notification reach one user and not the other.
DEMO_USERS = ("alice", "bob")


def index(request):
    """Notifications demo page."""
    return render(request, "notifications/index.html", {"demo_users": DEMO_USERS})


@require_POST
def sign_in(request, username):
    """Sign in as a demo user, creating them on first use. A demo, not a login."""
    if username not in DEMO_USERS:
        return redirect("notifications:index")
    user, _ = get_user_model().objects.get_or_create(username=username)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("notifications:index")


@require_POST
def sign_out(request):
    logout(request)
    return redirect("notifications:index")
