from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import path

from testproj.livesession.live_sessions import members

app_name = "historyprobe"


#: How many times the boosted POST form reached the server (#170)
POSTS: list[str] = []


def page(name):
    return lambda request: render(request, "historyprobe/page.html", {"name": name})


def post(request):
    # Post/redirect/get, as a boosted form should answer
    POSTS.append(request.method)
    if "away" in request.GET:
        # Off the site, as to a payment page or a sign-in: the other name of
        # the test server is another origin
        host = request.get_host()
        swap = ("127.0.0.1", "localhost") if "127.0.0.1" in host else ("localhost", "127.0.0.1")
        other = host.replace(*swap)
        return redirect(f"http://{other}/historyprobe/other/?tab=away")
    return redirect("/historyprobe/other/?tab=p")


#: The requests that reached the file views, as "METHOD path" (#227)
FILES: list[str] = []


def report(request):
    # A file to save: the browser downloads it and the page stays
    FILES.append(f"{request.method} {request.path}")
    response = HttpResponse("name,count\nbox,1\n", content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="report.csv"'
    return response


def notes(request):
    # Not a document but shown in place, as the browser shows plain text
    FILES.append(f"{request.method} {request.path}")
    return HttpResponse("plain notes", content_type="text/plain; charset=utf-8")


#: The requests that reached ``foreign/``, as (method, Origin header) (#262)
FOREIGN: list[tuple[str, str]] = []


def foreign(request):
    # Another origin's page -- the test server under its other name -- that
    # lets any origin read it, as a CDN or a public API does
    FOREIGN.append((request.method, request.headers.get("Origin", "")))
    response = HttpResponse(
        '<!DOCTYPE html><html><body><span data-testid="page">foreign</span></body></html>',
        content_type="text/html; charset=utf-8",
    )
    response["Access-Control-Allow-Origin"] = "*"
    return response


def hop(request):
    # An open redirect, as an app's "continue to" endpoint is: wherever ``to`` says
    return HttpResponseRedirect(request.GET["to"])


def bars(name):
    return lambda request: render(request, "historyprobe/bars.html", {"name": name})


urlpatterns = [
    path("", page("box"), name="index"),
    # Two pages with HistoryBar under one id (#170)
    path("bar-a/", bars("bar-a"), name="bar-a"),
    path("bar-b/", bars("bar-b"), name="bar-b"),
    path("other/", page("other"), name="other"),
    path("post/", post, name="post"),
    # Answers that are not a page to boost (#227)
    path("report/", report, name="report"),
    path("notes/", notes, name="notes"),
    # Destinations a URL policy judges (#262)
    path("aim/", lambda request: render(request, "historyprobe/aim_page.html"), name="aim"),
    path("foreign/", foreign, name="foreign"),
    path("hop/", hop, name="hop"),
    # Inside ls-members: a patch stays inside it, a push to other/ leaves it
    path("members/", members.view(page("members")), name="members"),
    # Components whose params_changed returns early on a query they already hold (#177)
    path("guarded/", lambda request: render(request, "historyprobe/guarded_page.html"), name="guarded"),
]
