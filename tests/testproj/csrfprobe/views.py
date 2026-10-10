from django.contrib.auth import get_user_model, login
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.views.decorators.http import require_POST

#: What the CSRF-checked view took, in order: (text, username)
POSTED: list[tuple[str, str]] = []


def index(request):
    return render(request, "csrfprobe/page.html")


@require_POST
def post(request):
    """Checked by CsrfViewMiddleware like any view: a missing or wrong token never gets here."""
    text = request.POST.get("text", "")
    POSTED.append((text, request.user.get_username()))
    return HttpResponse(format_html('<p data-testid="posted">posted {}</p>', text))


def sign_in(request, name: str):
    """Log in as ``name``: Django rotates the CSRF secret (``rotate_token``)."""
    user, _ = get_user_model().objects.get_or_create(username=name)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("csrfprobe:index")
