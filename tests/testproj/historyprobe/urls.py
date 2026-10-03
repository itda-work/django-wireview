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
    return redirect("/historyprobe/other/?tab=p")


urlpatterns = [
    path("", page("box"), name="index"),
    path("other/", page("other"), name="other"),
    path("post/", post, name="post"),
    # Inside ls-members: a patch stays inside it, a push to other/ leaves it
    path("members/", members.view(page("members")), name="members"),
]
