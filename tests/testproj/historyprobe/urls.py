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


urlpatterns = [
    path("", page("box"), name="index"),
    path("other/", page("other"), name="other"),
    path("post/", post, name="post"),
    # Inside ls-members: a patch stays inside it, a push to other/ leaves it
    path("members/", members.view(page("members")), name="members"),
]
