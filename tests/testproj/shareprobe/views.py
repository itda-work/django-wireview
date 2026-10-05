from django.contrib.auth import get_user_model, login
from django.shortcuts import redirect, render


def index(request):
    return render(request, "shareprobe/page.html")


def sign_in(request, name: str):
    """Log in as ``name``. Exists for the E2E suite: each browser context is another viewer."""
    user, _ = get_user_model().objects.get_or_create(username=name)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("shareprobe:index")
