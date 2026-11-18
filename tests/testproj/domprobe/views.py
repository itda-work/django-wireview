import typing as t

from django.http import HttpResponse
from django.shortcuts import redirect, render

#: What ``received`` took, in order: the method, each field with its values (``getlist``), the user
RECEIVED: list[dict[str, t.Any]] = []


def index(request):
    return render(request, "domprobe/page.html")


def streams(request):
    return render(request, "domprobe/streams_page.html")


def table(request):
    return render(request, "domprobe/table_page.html")


def payload(request):
    return render(request, "domprobe/payload_page.html")


def family(request):
    return render(request, "domprobe/family_page.html")


def http(request):
    return render(request, "domprobe/http_page.html")


def received(request):
    """Where the HTTP forms of DomHttp go. CsrfViewMiddleware checks a POST like any view's."""
    data = request.POST if request.method == "POST" else request.GET
    RECEIVED.append(
        {
            "method": request.method,
            "fields": {name: data.getlist(name) for name in data if name != "csrfmiddlewaretoken"},
            "user": request.user.get_username(),
        }
    )
    if data.get("away"):
        return redirect("domprobe:http")
    return HttpResponse('<p data-testid="received">received</p>')
