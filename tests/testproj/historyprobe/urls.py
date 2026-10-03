from django.shortcuts import render
from django.urls import path

app_name = "historyprobe"

urlpatterns = [
    path("", lambda request: render(request, "historyprobe/page.html", {"name": "box"}), name="index"),
    path("other/", lambda request: render(request, "historyprobe/page.html", {"name": "other"}), name="other"),
]
