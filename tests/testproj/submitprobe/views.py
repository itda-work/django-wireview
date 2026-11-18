from django.contrib.auth import get_user_model, login
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.views.decorators.http import require_POST

#: What the CSRF-checked login view took, in order: the POST fields and the query
POSTED: list[dict[str, object]] = []


def index(request):
    return render(request, "submitprobe/page.html")


def lock(request):
    return render(request, "submitprobe/lock_page.html")


@require_POST
def sign_in(request):
    """Checked by CsrfViewMiddleware like any view: a missing or wrong token never gets here."""
    fields = {key: request.POST.getlist(key) for key in request.POST}
    POSTED.append({"fields": fields, "query": request.GET.urlencode()})
    user, _ = get_user_model().objects.get_or_create(username=request.POST["username"])
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("submitprobe:welcome")


#: How many times each view that leaves the page where it is was posted to
STAYED: dict[str, int] = {"nothing": 0, "download": 0, "popup": 0}


@require_POST
def nothing(request):
    """204: the browser keeps the page it is on."""
    STAYED["nothing"] += 1
    return HttpResponse(status=204)


@require_POST
def download(request):
    """An attachment: the browser saves it and keeps the page it is on."""
    STAYED["download"] += 1
    response = HttpResponse(b"report", content_type="text/plain")
    response["Content-Disposition"] = 'attachment; filename="report.txt"'
    return response


@require_POST
def popup(request):
    STAYED["popup"] += 1
    return HttpResponse(format_html('<p data-testid="popup">{}</p>', request.POST.get("username", "")))


def welcome(request):
    """Whom the session cookie the login view set says the browser is."""
    return HttpResponse(format_html('<p data-testid="welcome">welcome {}</p>', request.user.get_username()))
