from django.shortcuts import render
from django.urls import path

app_name = "lossprobe"


def page(name: str, id: str):
    return lambda request: render(request, "lossprobe/page.html", {"name": name, "id": id})


urlpatterns = [
    path("", page("LossBox", "lbox"), name="index"),
    path("send/", page("LossSender", "lsend"), name="send"),
]
