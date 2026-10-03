from django.shortcuts import render
from django.urls import path

from testproj.livesession.live_sessions import members

app_name = "historyprobe"


def page(name):
    return lambda request: render(request, "historyprobe/page.html", {"name": name})


urlpatterns = [
    path("", page("box"), name="index"),
    path("other/", page("other"), name="other"),
    # Inside ls-members: a patch stays inside it, a push to other/ leaves it
    path("members/", members.view(page("members")), name="members"),
]
